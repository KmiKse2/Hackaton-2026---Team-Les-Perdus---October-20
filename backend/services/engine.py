"""Deterministic bank signals. Scores are weights, not calibrated probabilities."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import re
import unicodedata

PURPOSES = {"SALA": "salary", "RENT": "rent", "ELEC": "energy", "GASB": "energy", "WTER": "energy"}
KEYWORDS = {
    "salary": r"\b(salaire|salary|payroll)\b", "rent": r"\b(loyer|rent)\b",
    "commute": r"\b(sncb|nmbs|stib|mivb|de lijn|tec abonnement)\b",
    "furniture": r"\b(ikea|meubles|mobilier)\b", "energy": r"\b(energie|engie|luminus|electricite)\b",
    "flight": r"\b(brussels airlines|ryanair|easyjet|air france)\b", "hotel": r"\b(booking\.com|airbnb|hotel)\b",
}


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c)).casefold()


def account_number(value):
    return re.sub(r"\s", "", value or "").upper()


def classify(payload):
    if payload.get("purposeCode") in PURPOSES:
        return PURPOSES[payload["purposeCode"]], "purposeCode", "strong"
    text = normalized(" ".join(payload.get(k) or "" for k in ("creditorName", "debtorName", "remittanceInformationUnstructured")))
    for category, pattern in KEYWORDS.items():
        if re.search(pattern, text):
            return category, "counterparty_or_remittance", "weak"
    return "other", "unclassified", "unknown"


def analyze(accounts, balances, transactions, access, as_of, personalization=True):
    permissions = set(access["effective_permissions"])
    blocked = not {"accounts", "transactions"}.issubset(permissions)
    scoped = [a for a in accounts if a.resource_id in access["account_ids"]] if "accounts" in permissions else []
    eligible = {a.resource_id: a for a in scoped if a.details.get("usage") == "PRIV" and a.details["status"] == "enabled"}
    own_numbers = {account_number(a.details.get(k)) for a in scoped for k in ("iban", "bban") if a.details.get(k)}
    signals, events = [], []
    quality = {"excluded_accounts": len(scoped) - len(eligible), "pending_transactions": 0,
               "internal_transfers": 0, "unclassified_transactions": 0, "stale_balances": 0,
               "warnings": [], "coverage": [], "score_kind": "rule_weight_not_probability"}
    if blocked:
        quality["warnings"].append("Analysis paused: valid consent and accounts + transactions permissions are required.")
    for account in eligible.values() if not blocked else []:
        complete = bool(account.history_from and account.history_to and account.history_to >= as_of)
        days = (as_of - account.history_from).days if complete else 0
        quality["coverage"].append({"account_id": account.resource_id, "from": str(account.history_from) if account.history_from else None,
                                   "to": str(account.history_to) if account.history_to else None,
                                   "covered_days": days, "novelty_detection_allowed": complete and days >= 60})
        if not complete or days < 60:
            quality["warnings"].append(f"{account.resource_id} : insufficient history to establish a new payment.")
    rows = []
    if not blocked:
        for tx in transactions:
            if tx.account_id not in eligible:
                continue
            data = tx.payload
            if tx.booking_status == "pending":
                quality["pending_transactions"] += 1
                continue
            tx_date = date.fromisoformat(data["bookingDate"])
            if tx_date > as_of:
                continue
            amount = Decimal(data["transactionAmount"]["amount"])
            counterpart = data.get("debtorAccount" if amount > 0 else "creditorAccount") or {}
            internal = any(account_number(counterpart.get(k)) in own_numbers for k in ("iban", "bban") if counterpart.get(k))
            category, method, strength = classify(data)
            if internal:
                quality["internal_transfers"] += 1
            elif category == "other":
                quality["unclassified_transactions"] += 1
            rows.append({"id": tx.id, "account_id": tx.account_id, "data": data, "date": tx_date,
                         "amount": amount, "currency": data["transactionAmount"]["currency"], "internal": internal,
                         "category": category, "method": method, "strength": strength})
    cutoff = as_of - timedelta(days=30)

    def emit(account, kind, label, hits, strength="strong", metrics=None, limitations=None, fields=None):
        signal = {"id": f"{account.resource_id}:{kind}", "type": kind, "label": label,
                  "account_id": account.resource_id, "strength": strength, "transaction_ids": [r["id"] for r in hits],
                  "source_fields": fields or sorted({r["method"] for r in hits}),
                  "metrics": dict({"currency": account.details["currency"], "observation_count": len(hits)}, **(metrics or {})),
                  "limitations": limitations or [], "as_of": as_of.isoformat()}
        signals.append(signal)
        return signal

    for account in eligible.values() if not blocked and personalization else []:
        if account.details.get("cashAccountType") == "SVGS":
            emit(account, "savings_account", "Savings account present", [], fields=["cashAccountType"],
                 limitations=["Having an account does not establish an ability to save."])
        history = sorted([r for r in rows if r["account_id"] == account.resource_id and not r["internal"]], key=lambda r: r["date"])
        recent = [r for r in history if r["date"] > cutoff]
        previous = [r for r in history if r["date"] <= cutoff]
        enough = bool(account.history_from and account.history_to and account.history_to >= as_of and (as_of - account.history_from).days >= 60)
        local = {}
        for category, kind, label, credit in [
            ("salary", "new_salary", "New SALA-coded salary in the covered history", True),
            ("rent", "new_rent", "New RENT-coded payment in the covered history", False),
            ("energy", "new_energy", "New energy expense", False),
        ]:
            hits = [r for r in recent if r["category"] == category and (r["amount"] > 0 if credit else r["amount"] < 0)]
            old = [r for r in previous if r["category"] == category and (r["amount"] > 0 if credit else r["amount"] < 0)]
            reliable = [r for r in hits if r["method"] == "purposeCode"]
            if reliable and not old and enough:
                local[kind] = emit(account, kind, label, reliable, limitations=["New within the covered data only; ask the customer to confirm."])
            elif hits and category == "salary":
                emit(account, "salary_observed", "Payment suggesting a salary", hits, "strong" if reliable else "weak",
                     limitations=["Does not establish novelty, recurrence or a first job."])
        for category, label in [("commute", "Local transport payment"), ("furniture", "Furniture payment"),
                                ("flight", "Payment suggesting air travel"), ("hotel", "Payment suggesting accommodation")]:
            hits = [r for r in recent if r["category"] == category and r["amount"] < 0]
            if hits:
                local[category] = emit(account, category, label, hits, "weak",
                                       limitations=["Merchant or payment text match; actual purpose is unconfirmed."])
        salaries = defaultdict(list)
        for row in history:
            if row["data"].get("purposeCode") == "SALA" and row["amount"] > 0:
                identity = account_number((row["data"].get("debtorAccount") or {}).get("iban")) or normalized(row["data"].get("debtorName"))
                if identity:
                    salaries[identity].append(row)
        for group in salaries.values():
            if len(group) >= 2:
                a, b = group[-2:]
                if b["date"] > cutoff and 25 <= (b["date"] - a["date"]).days <= 35 and abs(b["amount"] - a["amount"]) / a["amount"] <= Decimal("0.2"):
                    emit(account, "recurring_salary", "Recurring monthly salary observed", [a, b],
                         metrics={"interval_days": (b["date"] - a["date"]).days},
                         fields=["purposeCode", "debtorAccount/debtorName", "bookingDate", "transactionAmount"],
                         limitations=["Recurrence based on two observations; future income is not guaranteed."])
                    break
        mandates = defaultdict(list)
        for row in history:
            if row["amount"] < 0 and row["data"].get("mandateId"):
                mandates[row["data"]["mandateId"]].append(row)
        for group in mandates.values():
            if len(group) >= 2 and group[-1]["date"] > cutoff and 25 <= (group[-1]["date"] - group[-2]["date"]).days <= 35:
                emit(account, "recurring_mandate", "Monthly debit with the same mandate", group[-2:],
                     fields=["mandateId", "bookingDate", "transactionAmount"])
                break
        rents = [r for r in history if r["data"].get("purposeCode") == "RENT" and r["amount"] < 0]
        if len(rents) >= 2:
            a, b = rents[-2:]
            def recipient(r):
                return account_number((r["data"].get("creditorAccount") or {}).get("iban")) or normalized(r["data"].get("creditorName"))
            increase = (abs(b["amount"]) / abs(a["amount"]) - 1) * 100
            if b["date"] > cutoff and 25 <= (b["date"] - a["date"]).days <= 35 and recipient(a) and recipient(a) == recipient(b) and increase >= 15:
                emit(account, "rent_increase", "Rent increase observed", [a, b], metrics={"increase_percent": str(round(increase, 2))},
                     fields=["purposeCode", "creditorAccount/creditorName", "bookingDate", "transactionAmount"],
                     limitations=["A payment increase does not prove a move."])
        rules = [("FIRST_JOB", {"new_salary": 55, "commute": 15}, {"new_salary", "commute"}),
                 ("MOVING", {"new_rent": 35, "furniture": 20, "new_energy": 20}, {"new_rent", "furniture", "new_energy"}),
                 ("TRAVEL", {"flight": 30, "hotel": 30}, {"flight", "hotel"})]
        for kind, weights, required in rules:
            if required.issubset(local):
                events.append({"type": kind, "score": sum(weights[s] for s in required),
                               "evidence": [dict(local[s], weight=w) for s, w in weights.items() if s in local]})

    groups = defaultdict(lambda: {"available": Decimal(0), "booked": Decimal(0), "has_available": False, "has_booked": False, "credit_limit_included": False, "credit_limit_unknown": False})
    if {"balances", "accounts"}.issubset(permissions):
        for account in eligible.values():
            fresh = {}
            for b in balances:
                if b.account_id != account.resource_id:
                    continue
                age = (as_of - date.fromisoformat(b.payload["referenceDate"])).days
                if 0 <= age <= 7:
                    fresh[b.balance_type] = b
                else:
                    quality["stale_balances"] += 1
            group = groups[account.details["currency"]]
            available = fresh.get("interimAvailable")
            booked_options = [fresh[k] for k in ("interimBooked", "closingBooked") if k in fresh]
            booked = max(booked_options, key=lambda b: (b.payload["referenceDate"], b.balance_type == "interimBooked"), default=None)
            if available:
                group["available"] += Decimal(available.payload["balanceAmount"]["amount"])
                group["has_available"] = True
                group["credit_limit_included"] |= available.payload.get("creditLimitIncluded") is True
                group["credit_limit_unknown"] |= available.payload.get("creditLimitIncluded") is None
            if booked:
                amount = Decimal(booked.payload["balanceAmount"]["amount"])
                group["booked"] += amount
                group["has_booked"] = True
                if not blocked and personalization and account.details.get("cashAccountType") == "CACC" and amount < 0:
                    emit(account, "negative_booked_balance", "Negative booked balance", [], metrics={"amount": str(amount)},
                         fields=["balanceType", "balanceAmount", "referenceDate"],
                         limitations=["An isolated negative balance does not establish financial difficulty."])
    if quality["stale_balances"]:
        quality["warnings"].append("Balances outside the 7-day freshness window are ignored; no estimate is made by adding transactions.")
    currencies = sorted(set(groups) | {r["currency"] for r in rows})
    financials = []
    for currency in currencies:
        group = groups[currency]
        month = [r for r in rows if r["currency"] == currency and not r["internal"] and (r["date"].year, r["date"].month) == (as_of.year, as_of.month)]
        financials.append({"currency": currency, "available": str(group["available"]) if group["has_available"] else None,
                           "booked": str(group["booked"]) if group["has_booked"] else None,
                           "credit_limit_included": group["credit_limit_included"], "credit_limit_unknown": group["credit_limit_unknown"],
                           "income": str(sum((r["amount"] for r in month if r["amount"] > 0), Decimal(0))) if not blocked else None,
                           "expenses": str(-sum((r["amount"] for r in month if r["amount"] < 0), Decimal(0))) if not blocked else None})
    primary = next((f for f in financials if f["currency"] == "EUR"), financials[0] if financials else {})
    financial = {"balance": primary.get("available"), "booked_balance": primary.get("booked"), "currency": primary.get("currency", "EUR"),
                 "income": primary.get("income"), "expenses": primary.get("expenses"), "period": as_of.strftime("%Y-%m"),
                 "credit_limit_included": primary.get("credit_limit_included", False), "credit_limit_unknown": primary.get("credit_limit_unknown", False),
                 "by_currency": financials, "balance_source": "bank_snapshot", "internal_transfers_excluded": True}
    unique = {}
    for event in events:
        if event["type"] not in unique or event["score"] > unique[event["type"]]["score"]:
            unique[event["type"]] = event
    return {"as_of": as_of.isoformat(), "blocked": blocked, "signals": signals, "events": list(unique.values()),
            "quality": quality, "financial": financial,
            "transactions": [{"id": r["id"], "account_id": r["account_id"], "date": str(r["date"]),
                              "merchant": r["data"].get("debtorName" if r["amount"] > 0 else "creditorName") or "Counterparty not supplied",
                              "amount": str(r["amount"]), "currency": r["currency"], "category": r["category"],
                              "internal_transfer": r["internal"], "booking_status": "booked"}
                             for r in sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True)]}
