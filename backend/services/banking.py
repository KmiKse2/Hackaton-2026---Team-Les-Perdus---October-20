"""Persistence, scoped consent checks and import of complete bank snapshots."""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import case, delete, or_, select, update

from ..models import Account, Balance, BalanceObservation, BankProfile, BankTransaction, Consent
from ..schemas import AccountAccess, BankingImport
from .engine import analyze


def today():
    return datetime.now(timezone.utc).date()


def access_for(db, customer_id):
    consent = db.get(Consent, customer_id)
    if consent is None:
        return {"status": "missing", "effective_permissions": [], "account_ids": [], "automatic_syncs_remaining": 0}
    data = consent.payload
    valid = data["consentStatus"] == "valid" and date.fromisoformat(data["validUntil"]) >= today()
    used = consent.sync_count if consent.sync_day == today() else 0
    return {"consent_id": consent.consent_id, "status": data["consentStatus"] if valid or data["consentStatus"] != "valid" else "expired",
            "valid_until": data["validUntil"], "permissions": data["permissions"],
            "effective_permissions": data["permissions"] if valid else [], "account_ids": consent.account_ids if valid else [],
            "frequency_per_day": data["frequencyPerDay"], "automatic_syncs_remaining": max(0, data["frequencyPerDay"] - used) if valid else 0}


def require_access(db, customer_id, permissions, account_ids=()):
    access = access_for(db, customer_id)
    if not set(permissions).issubset(access["effective_permissions"]) or not set(account_ids).issubset(access["account_ids"]):
        raise HTTPException(403, "Invalid consent, missing permission or account outside the authorised scope")
    return access


def set_consent(db, customer_id, payload: AccountAccess):
    # This is a local demo-management endpoint, NOT a real bank consent grant.
    for resource_id in payload.accountResourceIds:
        account = db.get(Account, resource_id)
        if account and account.customer_id != customer_id:
            raise HTTPException(403, "Account belongs to another customer")
    other = db.scalar(select(Consent).where(Consent.consent_id == payload.consentId, Consent.customer_id != customer_id))
    if other:
        raise HTTPException(409, "Consent ID already in use")
    consent = db.get(Consent, customer_id)
    if consent is None:
        consent = Consent(customer_id=customer_id, consent_id=payload.consentId, sync_count=0)
        db.add(consent)
    consent.consent_id = payload.consentId
    consent.payload = payload.model_dump(mode="json")
    consent.account_ids = list(dict.fromkeys(payload.accountResourceIds))
    db.flush()


def store_import(db, customer_id, payload: BankingImport, automatic=False):
    required = {"accounts"}
    if any(a.balances is not None or a.balanceHistory is not None for a in payload.accounts):
        required.add("balances")
    if any(a.transactions is not None for a in payload.accounts):
        required.add("transactions")
    access = require_access(db, customer_id, required, [a.details.resourceId for a in payload.accounts])
    consent = db.get(Consent, customer_id)
    if consent.consent_id != payload.consentId:
        raise HTTPException(403, "Consent does not match the customer")
    if automatic:
        if access["automatic_syncs_remaining"] < 1:
            raise HTTPException(429, "Daily automatic sync budget exhausted")
        day = today()
        result = db.execute(update(Consent).where(
            Consent.customer_id == customer_id,
            or_(Consent.sync_day.is_(None), Consent.sync_day != day,
                Consent.sync_count < consent.payload["frequencyPerDay"]),
        ).values(sync_day=day, sync_count=case((Consent.sync_day == day, Consent.sync_count + 1), else_=1)))
        if result.rowcount != 1:
            raise HTTPException(429, "Daily automatic sync budget exhausted")
    profile = db.get(BankProfile, customer_id)
    if profile is None:
        profile = BankProfile(customer_id=customer_id, as_of=payload.asOf)
        db.add(profile)
    elif payload.asOf < profile.as_of:
        raise HTTPException(409, "Snapshot predates the current analysis date")
    profile.as_of = payload.asOf
    for item in payload.accounts:
        resource_id = item.details.resourceId
        account = db.get(Account, resource_id)
        if account and account.customer_id != customer_id:
            raise HTTPException(403, "Account belongs to another customer")
        if account is None:
            account = Account(resource_id=resource_id, customer_id=customer_id)
            db.add(account)
        elif account.details["currency"] != item.details.currency and (item.balances is None or item.transactions is None):
            raise HTTPException(422, "Changing currency requires replacing balances and transactions together")
        account.details = item.details.model_dump(mode="json", exclude_none=True)
        if item.transactions is not None:
            account.history_from, account.history_to = item.historyFrom, item.historyTo
        db.flush()
        for balance in (item.balanceHistory or []) + (item.balances or []):
            record_balance(db, resource_id, balance.model_dump(mode="json", exclude_none=True))
        if item.balances is not None:
            db.execute(delete(Balance).where(Balance.account_id == resource_id))
            for balance in item.balances:
                db.add(Balance(account_id=resource_id, balance_type=balance.balanceType, payload=balance.model_dump(mode="json", exclude_none=True)))
        if item.transactions is not None:
            existing = {t.transaction_id: t for t in db.scalars(select(BankTransaction).where(BankTransaction.account_id == resource_id))}
            keep = set()
            for status in ("booked", "pending"):
                for tx in getattr(item.transactions, status):
                    keep.add(tx.transactionId)
                    row = existing.get(tx.transactionId)
                    # Never demote a posted transaction when an upstream snapshot is delayed.
                    if row is not None and row.booking_status == "booked" and status == "pending":
                        raise HTTPException(409, "A booked transaction cannot revert to pending")
                    if row is None:
                        row = BankTransaction(account_id=resource_id, transaction_id=tx.transactionId)
                        db.add(row)
                    row.booking_status, row.payload = status, tx.model_dump(mode="json", exclude_none=True)
            for key, row in existing.items():
                if key not in keep:
                    db.delete(row)
    db.flush()


def inspect(db, customer_id):
    access = access_for(db, customer_id)
    # Permission and scope are applied before loading raw financial payloads.
    accounts = list(db.scalars(select(Account).where(Account.customer_id == customer_id, Account.resource_id.in_(access["account_ids"])))) if "accounts" in access["effective_permissions"] else []
    ids = [a.resource_id for a in accounts]
    balances = list(db.scalars(select(Balance).where(Balance.account_id.in_(ids)))) if "balances" in access["effective_permissions"] else []
    transactions = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id.in_(ids)))) if "transactions" in access["effective_permissions"] else []
    profile = db.get(BankProfile, customer_id)
    from .context import personalization_allowed
    personalize = personalization_allowed(db, customer_id)
    analysis = analyze(accounts, balances, transactions, access, profile.as_of if profile else today(), personalization=personalize)
    for signal in analysis["signals"]:
        signal.update(value=True, confidence=.9 if signal["strength"] == "strong" else .65,
                      confidence_kind="heuristic_not_calibrated", timestamp=signal["as_of"],
                      source="transaction" if signal["transaction_ids"] else "account",
                      status="observed" if signal["strength"] == "strong" else "inferred")
    if not personalize:
        analysis["blocked"] = True
        analysis["signals"], analysis["events"] = [], []
        analysis["quality"]["warnings"].append("Personalisation consent is missing, expired or disabled.")
    return analysis, access, accounts, balances


def record_balance(db, account_id, payload):
    day = date.fromisoformat(payload["referenceDate"])
    row = db.scalar(select(BalanceObservation).where(BalanceObservation.account_id == account_id,
        BalanceObservation.balance_type == payload["balanceType"], BalanceObservation.reference_date == day))
    if row is None:
        row = BalanceObservation(account_id=account_id, balance_type=payload["balanceType"], reference_date=day)
        db.add(row)
    row.payload = payload
    db.flush()
