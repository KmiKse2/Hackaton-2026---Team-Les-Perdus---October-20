"""Synthetic bank-shaped fixtures and non-destructive V1 bootstrap."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from .models import BankProfile, Customer, Transaction
from .schemas import AccountAccess, BankingImport
from .services.banking import set_consent, store_import, today


def legacy_payload(row):
    credit = row.amount > 0
    data = {"transactionId": row.reference, "transactionAmount": {"amount": str(row.amount), "currency": "EUR"},
            "bookingDate": row.date.isoformat(), "valueDate": row.date.isoformat(),
            "debtorName" if credit else "creditorName": row.merchant,
            "remittanceInformationUnstructured": row.merchant,
            "bankTransactionCode": {"domain": "PMNT", "family": "RCDT" if credit else "ICDT"}}
    # Only for constructing fake demo data. The signal engine never trusts V1 category.
    code = {"salary": "SALA", "rent": "RENT", "energy": "ELEC"}.get(row.category)
    if code:
        data["purposeCode"] = code
    if row.category in {"subscription", "energy"}:
        data["mandateId"] = f"demo-{row.customer_id}-{row.category}"
    return data


def fixture(db, customer, include_scenario=False):
    cid = customer.id
    account_id, savings_id = f"demo-{cid}-current", f"demo-{cid}-savings"
    all_rows = list(db.scalars(select(Transaction).where(Transaction.customer_id == cid)))
    rows = all_rows if include_scenario else [r for r in all_rows if r.reference.startswith("seed-")]
    latest = max((r.date for r in rows), default=date(2026, 8, 31))
    as_of = date(latest.year, latest.month, monthrange(latest.year, latest.month)[1])
    balance = customer.opening_balance + sum((r.amount for r in rows), Decimal(0))
    current = {"resourceId": account_id, "iban": f"DEMO-CURRENT-{cid}", "currency": "EUR", "name": "Demo current account",
               "ownerName": customer.name, "product": "current", "cashAccountType": "CACC", "status": "enabled", "usage": "PRIV"}
    savings = dict(current, resourceId=savings_id, iban=f"DEMO-SAVINGS-{cid}", name="Demo savings account", product="savings", cashAccountType="SVGS")
    def balances(amount):
        return [{"balanceAmount": {"amount": str(amount), "currency": "EUR"}, "balanceType": kind,
                 "referenceDate": as_of.isoformat(), "creditLimitIncluded": False,
                 "lastChangeDateTime": f"{as_of}T12:00:00Z"} for kind in ("interimAvailable", "closingBooked")]
    return BankingImport.model_validate({"consentId": f"demo-consent-{cid}", "asOf": as_of,
        "accounts": [{"details": current, "balances": balances(balance),
                      "transactions": {"booked": [legacy_payload(r) for r in rows], "pending": []},
                      "historyFrom": "2026-07-01", "historyTo": as_of},
                     {"details": savings, "balances": balances(Decimal("500")),
                      "transactions": {"booked": [], "pending": []}, "historyFrom": "2026-07-01", "historyTo": as_of}]})


def bootstrap(db):
    for customer in db.scalars(select(Customer)):
        if db.get(BankProfile, customer.id) is not None:
            continue
        payload = fixture(db, customer, include_scenario=customer.simulated)
        set_consent(db, customer.id, AccountAccess(consentId=payload.consentId, consentStatus="valid",
                    validUntil=today() + timedelta(days=90), frequencyPerDay=4,
                    permissions=["accounts", "balances", "transactions"], accountResourceIds=[a.details.resourceId for a in payload.accounts]))
        store_import(db, customer.id, payload)
    db.commit()


def reset_demo(db, customer):
    # Explicit demo reset only; the consent is never silently re-granted.
    profile = db.get(BankProfile, customer.id)
    profile.as_of = date(2026, 8, 31)
    store_import(db, customer.id, fixture(db, customer))
