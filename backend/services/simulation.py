"""Append deterministic monthly demo data; never replace a customer's imported history."""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from ..models import Account, Balance, BankProfile, BankTransaction, Consent
from ..schemas import BankingImport
from .banking import require_access, store_import


def append_month(db, customer):
    cid = customer.id
    if (cid, customer.name) not in {(1, "Thomas"), (2, "Sophie"), (3, "Marc")}:
        raise HTTPException(422, "Monthly simulation is only available for the three demo profiles.")
    ids = [f"demo-{cid}-current", f"demo-{cid}-savings"]
    require_access(db, cid, ["accounts", "balances", "transactions"], ids)
    previous = db.get(BankProfile, cid).as_of
    if previous.day != monthrange(previous.year, previous.month)[1]:
        raise HTTPException(409, "Simulation requires a month-end snapshot. Reset the demo or import a complete month first.")
    year, month = (previous.year + 1, 1) if previous.month == 12 else (previous.year, previous.month + 1)
    end = date(year, month, monthrange(year, month)[1])
    stamp = end.strftime("%Y-%m")
    transactions = []

    def tx(day, merchant, amount, code=None, country=None):
        data = {"transactionId": f"monthly-{stamp}-{len(transactions)}", "bookingDate": date(year, month, day).isoformat(),
                "transactionAmount": {"amount": str(amount), "currency": "EUR"},
                "debtorName" if Decimal(amount) > 0 else "creditorName": merchant,
                "debtorAccount" if Decimal(amount) > 0 else "creditorAccount": {"iban": "DEMO-PARTY-" + merchant.upper().replace(' ', '-')},
                "remittanceInformationUnstructured": merchant}
        if code:
            data["purposeCode"] = code
        if country:
            data["merchantCountry"] = country
        transactions.append(data)

    tx(1, "ACME Belgium" if cid == 1 else "Monthly employer", "2650" if cid == 1 else "2800", "SALA")
    tx(2, "Colruyt", "-186.40")
    tx(3, "Spotify", "-10.99")
    tx(4, "Neighbourhood cafe", "-25.00")
    if cid == 1:
        tx(5, "SNCB subscription", "-105")
    if cid == 2:
        tx(5, "Apartment rent", "-850", "RENT")
        tx(6, "Energy contract", "-74", "ELEC")
        if not customer.simulated:
            tx(7, "IKEA Zaventem", "-620")
    if cid == 3 and (month - 9) % 3 == 0:
        tx(8, "Brussels Airlines", "-240", country="BE")
        tx(9, "Booking.com Lisbon", "-385", country="PT")

    imports = []
    for account_id in ids:
        account = db.get(Account, account_id)
        if not account or account.details.get("currency") != "EUR" or account.details.get("status") != "enabled" or account.details.get("usage") != "PRIV":
            raise HTTPException(409, "Demo accounts must be enabled private EUR accounts.")
        if not account.history_from or account.history_to != previous:
            raise HTTPException(409, "Demo accounts must have complete history through the current analysis date.")
        candidates = list(db.scalars(select(Balance).where(Balance.account_id == account_id, Balance.balance_type.in_(["closingBooked", "interimBooked"]))))
        current = max(candidates, key=lambda b: (b.payload["referenceDate"], b.balance_type == "interimBooked"), default=None)
        if not current or current.payload["referenceDate"] != previous.isoformat():
            raise HTTPException(409, "A month-end booked balance is required for every demo account.")
        amount = Decimal(current.payload["balanceAmount"]["amount"])
        stored = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == account_id)))
        added = transactions if account_id == ids[0] else []
        if any(t.transaction_id in {a["transactionId"] for a in added} for t in stored):
            raise HTTPException(409, "This simulation month already exists. Reload the current state.")
        history = []
        for day in range(1, end.day + 1):
            day_date = date(year, month, day).isoformat()
            amount += sum((Decimal(t["transactionAmount"]["amount"]) for t in added if t["bookingDate"] == day_date), Decimal(0))
            history.append({"balanceAmount": {"amount": str(amount), "currency": "EUR"}, "balanceType": "closingBooked", "referenceDate": day_date, "creditLimitIncluded": False})
        imports.append({"details": account.details, "balances": [history[-1], dict(history[-1], balanceType="interimAvailable")],
                        "balanceHistory": history, "historyFrom": account.history_from, "historyTo": end,
                        "transactions": {"booked": [t.payload for t in stored if t.booking_status == "booked"] + added,
                                         "pending": [t.payload for t in stored if t.booking_status == "pending"]}})
    payload = BankingImport.model_validate({"consentId": db.get(Consent, cid).consent_id, "asOf": end, "accounts": imports})
    store_import(db, cid, payload)
    customer.simulated = True
    db.flush()
    return stamp
