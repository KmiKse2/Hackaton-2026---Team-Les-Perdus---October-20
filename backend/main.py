import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .bank_seed import bootstrap, fixture, legacy_payload, reset_demo
from .database import Base, SessionLocal, engine, get_db
from .models import Account, Balance, BalanceObservation, BankProfile, BankTransaction, CategoryFeedback, ChatMessage, Consent, Customer, Insight, SimulationMonth, Transaction
from .context_schemas import CategoryResponse, ContextImport, PersonalizationConsent
from .schemas import AccountAccess, BankingImport
from .seed import SCENARIOS, seed
from .services.banking import access_for, inspect, record_balance, require_access, set_consent, store_import, today
from .services.context import bootstrap_context, update_context, update_personalization
from .services.catalog import METADATA, SIGNALS
from .services.customer_state import STATE_LABELS, compute_catalog
from .services.kate import handoff, personalize
from .services.simulation import append_month
from .services.solutions import chat_view, proposals, reply


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        bootstrap(db)
        bootstrap_context(db)
        for old in db.scalars(select(Insight).where(Insight.status.in_(["confirmed", "dismissed"]))):
            category = {"MOVING": "moving", "TRAVEL": "travelling"}.get(old.type)
            if category and db.scalar(select(CategoryFeedback).where(CategoryFeedback.customer_id == old.customer_id, CategoryFeedback.category == category)) is None:
                save_category_response(db, old.customer_id, category, "confirmed" if old.status == "confirmed" else "rejected")
        for balance in db.scalars(select(Balance)):
            record_balance(db, balance.account_id, balance.payload)
        for customer in db.scalars(select(Customer)):
            refresh_insights(db, customer.id)
        db.commit()
    yield


app = FastAPI(title="LifeFlow · Customer Intelligence for Kate", version="0.4.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Demo-Token"])


def authorize(x_demo_token: str = Header(default="")):
    expected = os.getenv("DEMO_API_TOKEN", "")
    if expected and not secrets.compare_digest(x_demo_token, expected):
        raise HTTPException(401, "Invalid demo token")


protected = [Depends(authorize)]


def customer_or_404(db, customer_id, lock=False):
    query = select(Customer).where(Customer.id == customer_id)
    customer = db.scalar(query.with_for_update() if lock else query)
    if customer is None:
        raise HTTPException(404, "Customer not found")
    return customer


def refresh_insights(db, customer_id):
    analysis, *_ = inspect(db, customer_id)
    existing = {item.type: item for item in db.scalars(select(Insight).where(Insight.customer_id == customer_id))}
    for event in analysis["events"]:
        item = existing.get(event["type"])
        if item is None:
            db.add(Insight(customer_id=customer_id, **event, personalization=personalize(event)))
        else:
            previous_account = item.evidence[0].get("account_id") if item.evidence else None
            if previous_account and previous_account != event["evidence"][0]["account_id"]:
                item.status = "pending"
            item.score, item.evidence = event["score"], event["evidence"]
            item.personalization = personalize(event)
    # Inactive events remain stored to remember dismissals; reads only return active ones.
    db.flush()


def snapshot(db, customer):
    analysis, access, accounts, balances = inspect(db, customer.id)
    stored = {i.type: i for i in db.scalars(select(Insight).where(Insight.customer_id == customer.id))}
    events = []
    for event in sorted(analysis["events"], key=lambda e: (-e["score"], e["type"])):
        saved = stored.get(event["type"])
        if saved:
            events.append(dict(event, id=saved.id, status=saved.status, personalization=personalize(event)))
    profile = compute_catalog(db, customer.id, analysis, access, accounts, balances)
    kate = handoff(analysis, events)
    kate["customer_profile"] = profile
    options = proposals(events, profile)
    kate["solution_proposals"] = options
    kate["schema_version"] = "2.0"
    chat = chat_view(db, customer.id, profile, options)
    timeline_allowed = {"accounts", "balances", "transactions"}.issubset(access["effective_permissions"]) and {f"demo-{customer.id}-current", f"demo-{customer.id}-savings"}.issubset(access["account_ids"])
    timeline = [dict(row.summary, month=row.month) for row in db.scalars(select(SimulationMonth).where(SimulationMonth.customer_id == customer.id).order_by(SimulationMonth.month))] if timeline_allowed else []
    if profile["blocked"]:
        timeline = [dict(row, events=[], categories=[]) for row in timeline]
    return {"customer": {"id": customer.id, "name": customer.name, "persona": customer.persona,
                         "initials": customer.initials, "simulated": customer.simulated},
            "financial": analysis["financial"], "signals": analysis["signals"], "events": events,
            "transactions": analysis["transactions"], "analysis_date": analysis["as_of"],
            "consent": access, "data_quality": analysis["quality"],
            "accounts": [{**{k: a.details.get(k) for k in ("resourceId", "name", "currency", "cashAccountType", "status", "usage")},
                          "name": {"Compte courant démo": "Demo current account", "Épargne démo": "Demo savings account"}.get(a.details.get("name"), a.details.get("name")),
                          "balances": [b.payload for b in balances if b.account_id == a.resource_id]} for a in accounts],
            "customer_profile": profile, "kate_context": kate, "chat": chat, "simulation_timeline": timeline}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "mode": "synthetic-demo", "version": "0.4.0", "kate": "contract_only"}


@app.get("/api/signal-catalog", dependencies=protected)
def signal_catalog():
    return {"signals": [{"key": k, "label": v[0], "source": v[1]} for k, v in SIGNALS.items()],
            "metadata": METADATA, "confidence_note": "Heuristic confidence scores are not calibrated probabilities."}


@app.get("/api/customers/{customer_id}/profile", dependencies=protected)
def customer_profile(customer_id: int, db: Session = Depends(get_db)):
    return snapshot(db, customer_or_404(db, customer_id))["customer_profile"]


@app.post("/api/customers/{customer_id}/context", dependencies=protected)
def import_context(customer_id: int, payload: ContextImport, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    update_context(db, customer_id, payload)
    db.commit()
    return snapshot(db, customer)


@app.post("/api/customers/{customer_id}/personalization-consent", dependencies=protected)
def personalization_consent(customer_id: int, payload: PersonalizationConsent, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    update_personalization(db, customer_id, payload)
    refresh_insights(db, customer_id)
    db.commit()
    return snapshot(db, customer)


def save_category_response(db, customer_id, category, status):
    row = db.scalar(select(CategoryFeedback).where(CategoryFeedback.customer_id == customer_id, CategoryFeedback.category == category))
    if row is None:
        row = CategoryFeedback(customer_id=customer_id, category=category)
        db.add(row)
    row.status, row.observed_at, row.valid_until = status, today(), today() + timedelta(days=90)
    db.flush()


@app.post("/api/customers/{customer_id}/categories/{category}/feedback", dependencies=protected)
def category_response(customer_id: int, category: str, payload: CategoryResponse, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    state = snapshot(db, customer)
    if state["customer_profile"]["blocked"]:
        raise HTTPException(403, "Personalisation is not authorised")
    current = next((c for c in state["customer_profile"]["categories"] if c["key"] == category), None)
    if category not in STATE_LABELS or not current or current["source"] not in {"inferred", "kate_confirmation"}:
        raise HTTPException(422, "Only inferred categories can be confirmed or dismissed here")
    save_category_response(db, customer_id, category, payload.status)
    event_type = {"moving": "MOVING", "travelling": "TRAVEL"}.get(category)
    if event_type:
        item = db.scalar(select(Insight).where(Insight.customer_id == customer_id, Insight.type == event_type))
        if item:
            item.status = "confirmed" if payload.status == "confirmed" else "dismissed"
    db.commit()
    return snapshot(db, customer)


@app.get("/api/customers", dependencies=protected)
def customers(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "persona": c.persona, "initials": c.initials} for c in db.scalars(select(Customer).order_by(Customer.id))]


@app.get("/api/customers/{customer_id}", dependencies=protected)
def customer_state(customer_id: int, db: Session = Depends(get_db)):
    return snapshot(db, customer_or_404(db, customer_id))


@app.get("/api/customers/{customer_id}/transactions", dependencies=protected)
def transactions(customer_id: int, db: Session = Depends(get_db)):
    return snapshot(db, customer_or_404(db, customer_id))["transactions"]


@app.get("/api/customers/{customer_id}/insights", dependencies=protected)
def insights(customer_id: int, db: Session = Depends(get_db)):
    state = snapshot(db, customer_or_404(db, customer_id))
    return {key: state[key] for key in ("signals", "events", "data_quality")}


@app.get("/api/customers/{customer_id}/kate-context", dependencies=protected)
def kate_context(customer_id: int, db: Session = Depends(get_db)):
    return snapshot(db, customer_or_404(db, customer_id))["kate_context"]


@app.post("/api/customers/{customer_id}/consent", dependencies=protected)
def update_consent(customer_id: int, payload: AccountAccess, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    set_consent(db, customer_id, payload)
    refresh_insights(db, customer_id)
    db.commit()
    return snapshot(db, customer)


@app.post("/api/customers/{customer_id}/banking", dependencies=protected)
def import_banking(customer_id: int, payload: BankingImport, automatic: bool = False, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    store_import(db, customer_id, payload, automatic=automatic)
    refresh_insights(db, customer_id)
    db.commit()
    return snapshot(db, customer)


@app.get("/api/accounts", dependencies=protected)
def accounts(customer_id: int, db: Session = Depends(get_db)):
    customer_or_404(db, customer_id)
    access = require_access(db, customer_id, ["accounts"])
    return {"accounts": [a.details for a in db.scalars(select(Account).where(Account.customer_id == customer_id, Account.resource_id.in_(access["account_ids"])))]}


def scoped_account(db, customer_id, account_id, permission):
    account = db.get(Account, account_id)
    if account is None or account.customer_id != customer_id:
        raise HTTPException(404, "Account not found for this customer")
    require_access(db, customer_id, [permission], [account_id])
    return account


@app.get("/api/accounts/{account_id}", dependencies=protected)
def account_details(account_id: str, customer_id: int, db: Session = Depends(get_db)):
    return scoped_account(db, customer_id, account_id, "accounts").details


@app.get("/api/accounts/{account_id}/balances", dependencies=protected)
def account_balances(account_id: str, customer_id: int, db: Session = Depends(get_db)):
    scoped_account(db, customer_id, account_id, "balances")
    return {"balances": [b.payload for b in db.scalars(select(Balance).where(Balance.account_id == account_id))]}


@app.get("/api/accounts/{account_id}/transactions", dependencies=protected)
def account_report(account_id: str, customer_id: int, db: Session = Depends(get_db)):
    scoped_account(db, customer_id, account_id, "transactions")
    txs = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == account_id).order_by(BankTransaction.id)))
    return {"transactions": {status: [t.payload for t in txs if t.booking_status == status] for status in ("booked", "pending")}}


@app.get("/api/consents/{consent_id}", dependencies=protected)
def consent_details(consent_id: str, customer_id: int, db: Session = Depends(get_db)):
    consent = db.get(Consent, customer_id)
    if consent is None or consent.consent_id != consent_id:
        raise HTTPException(404, "Consent not found for this customer")
    return dict(consent.payload, consentStatus=access_for(db, customer_id)["status"])


class SimulationRequest(BaseModel):
    months: StrictInt = Field(ge=1, le=12)
    expected_as_of: date


@app.post("/api/customers/{customer_id}/simulate", dependencies=protected)
def simulate(customer_id: int, payload: SimulationRequest | None = None, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    require_access(db, customer_id, ["accounts", "balances", "transactions"])
    if payload is not None:
        if db.get(BankProfile, customer_id).as_of != payload.expected_as_of:
            raise HTTPException(409, "The analysis date has changed. Reload before simulating again.")
        for _ in range(payload.months):
            month = append_month(db, customer)
            refresh_insights(db, customer_id)
            state = snapshot(db, customer)
            db.add(SimulationMonth(customer_id=customer_id, month=month, summary={
                "financial": state["financial"], "events": [e["type"] for e in state["events"]],
                "categories": [c["key"] for c in state["customer_profile"]["categories"] if c["status"] not in {"rejected", "expired"}]}))
            db.flush()
        db.commit()
        return snapshot(db, customer)
    if not customer.simulated:
        if customer_id not in SCENARIOS:
            raise HTTPException(404, "No demo scenario exists for this customer")
        # V1 rows remain a synthetic fixture archive, never the signal engine input.
        for index, (merchant, amount, category) in enumerate(SCENARIOS[customer_id]):
            reference = f"scenario-{index}"
            if db.scalar(select(Transaction.id).where(Transaction.customer_id == customer_id, Transaction.reference == reference)) is None:
                db.add(Transaction(customer_id=customer_id, date=date(2026, 9, index + 1), merchant=merchant,
                                   amount=Decimal(amount), category=category, reference=reference))
        customer.simulated = True
        db.flush()
        store_import(db, customer_id, fixture(db, customer, include_scenario=True))
        refresh_insights(db, customer_id)
        db.commit()
    return snapshot(db, customer)


@app.post("/api/customers/{customer_id}/reset", dependencies=protected)
def reset(customer_id: int, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    require_access(db, customer_id, ["accounts", "balances", "transactions"])
    reset_demo(db, customer)
    db.execute(delete(BalanceObservation).where(BalanceObservation.account_id.in_([f"demo-{customer_id}-current", f"demo-{customer_id}-savings"]), BalanceObservation.reference_date > date(2026, 8, 31)))
    db.execute(delete(SimulationMonth).where(SimulationMonth.customer_id == customer_id))
    db.execute(delete(ChatMessage).where(ChatMessage.customer_id == customer_id))
    db.execute(delete(Insight).where(Insight.customer_id == customer_id))
    db.execute(delete(CategoryFeedback).where(CategoryFeedback.customer_id == customer_id))
    customer.simulated = False
    refresh_insights(db, customer_id)
    db.commit()
    return snapshot(db, customer)


class Feedback(BaseModel):
    status: Literal["confirmed", "dismissed"]


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=1000)
    proposal_id: str | None = Field(default=None, max_length=80)


@app.post("/api/customers/{customer_id}/chat", dependencies=protected)
def chat_message(customer_id: int, payload: ChatRequest, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    state = snapshot(db, customer)
    if state["chat"]["blocked"]:
        raise HTTPException(403, "Personalization consent and bank access are required for this chat.")
    options = state["chat"]["proposals"]
    option = next((p for p in options if p["id"] == payload.proposal_id), None)
    if payload.proposal_id and not option:
        raise HTTPException(409, "This proposal is no longer available. Reload your current situation.")
    message = payload.message.strip() or (option["title"] if option else "")
    if not message:
        raise HTTPException(422, "Enter a message or choose a proposal.")
    proposal_id, answer = reply(message, payload.proposal_id, options)
    if proposal_id is None and not payload.proposal_id and state["chat"]["messages"]:
        previous_id = state["chat"]["messages"][-1]["proposal_id"]
        previous = next((p for p in options if p["id"] == previous_id), None)
        if previous:
            proposal_id = previous_id
            answer = "Your details have been saved in this demo conversation for " + previous["title"].lower() + ". A connected Kate service would check the details and return the available options before asking for your approval. No live quote, purchase or order has been made."
    for role, text in (("user", message), ("assistant", answer)):
        db.add(ChatMessage(customer_id=customer_id, role=role, text=text, proposal_id=proposal_id, as_of=date.fromisoformat(state["analysis_date"])))
    db.commit()
    return snapshot(db, customer)


@app.post("/api/customers/{customer_id}/insights/{insight_id}/feedback", dependencies=protected)
def feedback(customer_id: int, insight_id: int, payload: Feedback, db: Session = Depends(get_db)):
    customer = customer_or_404(db, customer_id, lock=True)
    require_access(db, customer_id, ["accounts", "transactions"])
    item = db.scalar(select(Insight).where(Insight.id == insight_id, Insight.customer_id == customer_id))
    active = {e["type"] for e in inspect(db, customer_id)[0]["events"]}
    if item is None or item.type not in active:
        raise HTTPException(404, "Active event not found for this customer")
    item.status = payload.status
    category = {"MOVING": "moving", "TRAVEL": "travelling"}.get(item.type)
    if category:
        save_category_response(db, customer_id, category, "confirmed" if payload.status == "confirmed" else "rejected")
    db.commit()
    return snapshot(db, customer)


class NewTransaction(BaseModel):
    customer_id: int = Field(gt=0)
    date: date
    merchant: str = Field(min_length=1, max_length=100, pattern=r"\S")
    amount: Decimal = Field(max_digits=12, decimal_places=2, allow_inf_nan=False)
    category: Literal["salary", "support", "commute", "rent", "furniture", "energy", "flight", "hotel", "groceries", "subscription", "dining", "other"]
    reference: str = Field(min_length=1, max_length=80, pattern=r"^user-[a-zA-Z0-9_-]+$")


@app.post("/api/transactions", dependencies=protected, status_code=201, deprecated=True)
def add_transaction(payload: NewTransaction, db: Session = Depends(get_db)):
    """Legacy synthetic-data helper; use /banking for actual bank-shaped snapshots."""
    customer = customer_or_404(db, payload.customer_id, lock=True)
    account_id = f"demo-{customer.id}-current"
    scoped_account(db, customer.id, account_id, "transactions")
    data = legacy_payload(Transaction(**payload.model_dump()))
    db.add(BankTransaction(account_id=account_id, transaction_id=payload.reference, booking_status="booked", payload=data))
    try:
        db.flush()
        profile = db.get(BankProfile, customer.id)
        profile.as_of = max(profile.as_of, payload.date)
        refresh_insights(db, customer.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Reference already in use for this customer")
    return snapshot(db, customer)


dashboard = Path(__file__).resolve().parent.parent / "dashboard"
app.mount("/assets", StaticFiles(directory=dashboard), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(dashboard / "index.html", headers={"Cache-Control": "no-store"})
