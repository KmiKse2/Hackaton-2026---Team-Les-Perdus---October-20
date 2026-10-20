"""Catalog computations and explainable customer segmentation over supplied sources."""
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, pstdev

from sqlalchemy import select

from ..models import BalanceObservation, BankTransaction, CategoryFeedback, CustomerContext, Insight
from .banking import today
from .catalog import empty_catalog, set_signal
from .engine import account_number, classify, normalized

D = Decimal
ZERO = D(0)
LOANS = {"mortgage", "car_loan", "personal_loan"}
STATE_LABELS = {"student": "Student", "young_professional": "Young professional", "first_job": "Possible first job",
                "moving": "Moving", "home_purchase": "Home purchase", "travelling": "Planning a trip",
                "financial_stress": "Possible cash flow pressure", "wealth_growth": "Growing savings"}


def month_start(day):
    return day.replace(day=1)


def shift_month(day, offset):
    index = day.year * 12 + day.month - 1 + offset
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def month_end(day):
    return day.replace(day=monthrange(day.year, day.month)[1])


def fresh(fact, as_of):
    return bool(fact and date.fromisoformat(fact["observed_at"]) <= as_of and
                (not fact.get("valid_until") or date.fromisoformat(fact["valid_until"]) >= max(as_of, today())))


def age_at(birth, day):
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def age_group(age):
    for ceiling, label in [(17, "under_18"), (25, "18-25"), (35, "26-35"), (50, "36-50"), (65, "51-65")]:
        if age <= ceiling:
            return label
    return "66+"


def income_source(payload):
    code = payload.get("purposeCode")
    codes = {"SALA": "salary", "PENS": "pension", "STDY": "student_grant", "SSBE": "social_benefit",
             "UBEN": "unemployment_benefit", "DIVD": "investment_income", "INTE": "investment_income"}
    if code in codes:
        return codes[code], .95
    text = normalized(" ".join(payload.get(k) or "" for k in ("debtorName", "remittanceInformationUnstructured")))
    for words, kind in [(('bourse', 'student grant', 'scholarship'), 'student_grant'),
                        (('salaire', 'salary', 'payroll'), 'salary'), (('pension', 'retraite'), 'pension'),
                        (('chomage', 'unemployment'), 'unemployment_benefit'), (('allocations', 'social benefit'), 'social_benefit'),
                        (('freelance', 'honoraires'), 'freelance_income'), (('famille', 'parents', 'family'), 'family_transfer')]:
        if any(word in text for word in words):
            return kind, .65
    return "unknown", .3


def prepare_rows(db, accounts, as_of):
    own = {account_number(a.details.get(k)): a for a in accounts for k in ("iban", "bban") if a.details.get(k)}
    eligible = {a.resource_id: a for a in accounts if a.details.get("usage") == "PRIV" and a.details["status"] == "enabled"}
    result = []
    for tx in db.scalars(select(BankTransaction).where(BankTransaction.account_id.in_(eligible), BankTransaction.booking_status == "booked")):
        p = tx.payload
        day = date.fromisoformat(p["bookingDate"])
        if day > as_of:
            continue
        amount = D(p["transactionAmount"]["amount"])
        number = p.get("debtorAccount" if amount > 0 else "creditorAccount") or {}
        counterpart = next((own[account_number(number[k])] for k in ("iban", "bban") if number.get(k) and account_number(number[k]) in own), None)
        identity = next((account_number(number[k]) for k in ("iban", "bban") if number.get(k)), None)
        identity = identity or normalized(p.get("debtorName" if amount > 0 else "creditorName"))
        category, method, _ = classify(p)
        source, confidence = income_source(p) if amount > 0 else (None, None)
        result.append({"id": tx.id, "account": eligible[tx.account_id], "date": day, "amount": amount,
                       "currency": p["transactionAmount"]["currency"], "category": category, "method": method,
                       "source_type": source, "source_confidence": confidence, "payload": p,
                       "counterpart": counterpart, "identity": identity})
    return sorted(result, key=lambda r: (r["date"], r["id"]))


def recurring(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["identity"] and not r["counterpart"] and r["amount"]:
            groups[(r["account"].resource_id, r["currency"], r["identity"], r["amount"] > 0)].append(r)
    patterns = []
    for group in groups.values():
        if len(group) < 2:
            continue
        tail = group[-3:]
        amounts = [abs(r["amount"]) for r in tail]
        intervals = [(b["date"] - a["date"]).days for a, b in zip(tail, tail[1:])]
        if all(25 <= gap <= 35 for gap in intervals) and max(amounts) <= min(amounts) * D("1.2"):
            patterns.append(tail)
    return patterns


def amounts_by_currency(rows):
    values = defaultdict(Decimal)
    for r in rows:
        values[r["currency"]] += r["amount"]
    return values


def compute_catalog(db, customer_id, analysis, access, accounts, balances):
    as_of = date.fromisoformat(analysis["as_of"])
    context = db.get(CustomerContext, customer_id)
    catalog = empty_catalog(as_of, analysis["blocked"])
    consent = context.personalization if context else {"value": False, "source": "declared", "observed_at": str(today())}
    from .context import personalization_allowed
    set_signal(catalog, "personalization_consent", personalization_allowed(db, customer_id), source=consent["source"],
               status="confirmed", timestamp=consent["observed_at"])
    if analysis["blocked"]:
        return {"signals": catalog, "categories": [], "synthetic": bool(context and context.synthetic), "blocked": True}
    attrs = context.attributes if context else {}
    for name, fact in attrs.items():
        target = "age_group" if name == "date_of_birth" else name
        if target not in catalog:
            continue
        if fresh(fact, as_of):
            value = age_group(age_at(date.fromisoformat(fact["value"]), as_of)) if name == "date_of_birth" else fact["value"]
            set_signal(catalog, target, value, source=fact["source"], status="confirmed", timestamp=fact["observed_at"],
                       limitations=["Declared or source-provided information; 1.0 does not represent independent verification."])
        else:
            catalog[target].update(status="expired", availability="expired", limitations=["Information is future-dated or expired at the analysis/access date."])
    eligible = [a for a in accounts if a.details.get("usage") == "PRIV" and a.details["status"] == "enabled"]
    rows = prepare_rows(db, accounts, as_of)
    external = [r for r in rows if not r["counterpart"]]
    cutoff = as_of - timedelta(days=30)
    recent = [r for r in external if r["date"] > cutoff]
    patterns = recurring(external)
    incoming_patterns = [g for g in patterns if g[-1]["amount"] > 0]
    expense_patterns = [g for g in patterns if g[-1]["amount"] < 0]
    salary_patterns = [g for g in incoming_patterns if all(r["payload"].get("purposeCode") == "SALA" for r in g)]
    last_month = month_start(as_of if as_of == month_end(as_of) else shift_month(as_of, -1))
    months = [shift_month(last_month, offset) for offset in (-2, -1, 0)]
    def covered(start, end):
        return bool(eligible) and all(a.history_from and a.history_to and a.history_from <= start and a.history_to >= end for a in eligible)
    complete = [m for m in months if covered(m, month_end(m))]
    currencies = sorted({a.details["currency"] for a in eligible})
    ids = lambda items: [r["id"] for r in items]
    def put(key, value, evidence=(), confidence=.9, **kwargs):
        return set_signal(catalog, key, value, evidence=list(evidence), confidence=confidence, **kwargs)
    def percent(current, previous):
        return str(round((current - previous) / previous * 100, 2)) if previous else None

    if len(complete) >= 2 and incoming_patterns and catalog["monthly_income"]["availability"] != "available":
        recurring_ids = {r["id"] for g in incoming_patterns for r in g}
        recurring_rows = [r for r in external if r["id"] in recurring_ids and month_start(r["date"]) in complete]
        totals = amounts_by_currency(recurring_rows)
        put("monthly_income", [{"currency": c, "amount": str(round(v / len(complete), 2)), "months": len(complete)} for c, v in sorted(totals.items())], ids(recurring_rows), .9,
            limitations=["Average observed recurring inflows across fully covered months, not guaranteed income."])
    if recent:
        sources = sorted({r["source_type"] for r in recent if r["amount"] > 0})
        if sources:
            put("income_source_type", sources, ids([r for r in recent if r["amount"] > 0]), .65, status="inferred",
                limitations=["Structured codes or labels; income type does not establish employment status."])
        grants = [r for r in recent if r["source_type"] == "student_grant"]
        put("student_grant_detected", bool(grants), ids(grants), .75, status="inferred",
            limitations=["Code or text match; institutional payer is not verified in this PoC."])
    new_salary = []
    for group in salary_patterns:
        first = group[0]
        account = first["account"]
        prior = [r for r in external if r["account"].resource_id == account.resource_id and r["date"] < first["date"] and r["payload"].get("purposeCode") == "SALA" and r["amount"] > 0]
        if group[-1]["date"] > cutoff and account.history_from and (first["date"] - account.history_from).days >= 30 and not prior:
            new_salary.extend(group)
    if covered(as_of - timedelta(days=89), as_of):
        put("new_recurring_salary", bool(new_salary), ids(new_salary), .9,
            limitations=["At least two occurrences and prior coverage without salary; novelty is limited to supplied history."])
    missed = []
    for group in salary_patterns:
        due = shift_month(group[-1]["date"], 1)
        cycles = 0
        while due + timedelta(days=7) <= as_of:
            cycles += 1
            due = shift_month(due, 1)
        if cycles and covered(group[0]["date"], as_of):
            missed.append({"currency": group[-1]["currency"], "missed_cycles": cycles})
    if salary_patterns and covered(min(g[0]["date"] for g in salary_patterns), as_of):
        put("salary_stopped", {"detected": bool(missed), "patterns": missed}, ids([r for g in salary_patterns for r in g]), .8,
            limitations=["Seven-day grace period after the expected date; a missing salary does not mean unemployment."])
    if patterns:
        put("recurring_expenses", [{"currency": g[-1]["currency"], "amount": str(round(sum(abs(r["amount"]) for r in g) / len(g), 2)),
             "category": g[-1]["category"], "occurrences": len(g), "last_date": str(g[-1]["date"])} for g in expense_patterns],
             ids([r for g in expense_patterns for r in g]), .85)
    rents = [g for g in expense_patterns if all(r["payload"].get("purposeCode") == "RENT" for r in g)]
    if covered(as_of - timedelta(days=89), as_of):
        new_rents = [g for g in rents if g[-1]["date"] > cutoff and not any(r["date"] < g[0]["date"] and r["category"] == "rent" and r["amount"] < 0 for r in external)]
        put("new_rent_payment", bool(new_rents), ids([r for g in new_rents for r in g]), .85,
            limitations=["One rent payment is not enough to establish a new recurring payment."])

    if last_month in complete:
        current = [r for r in external if month_start(r["date"]) == last_month]
        incomes = {c: sum((r["amount"] for r in current if r["currency"] == c and r["amount"] > 0), ZERO) for c in currencies}
        expenses = {c: -sum((r["amount"] for r in current if r["currency"] == c and r["amount"] < 0), ZERO) for c in currencies}
        put("monthly_expenses", [{"currency": c, "amount": str(v), "month": str(last_month)[:7]} for c, v in expenses.items()], ids(current), 1,
            limitations=["Totals cover authorised accounts and complete months only, excluding own transfers."])
        savings_flows = defaultdict(Decimal)
        withdrawals = []
        for r in rows:
            if month_start(r["date"]) != last_month:
                continue
            if r["account"].details.get("cashAccountType") == "CACC" and r["counterpart"] and r["counterpart"].details.get("cashAccountType") == "SVGS":
                savings_flows[r["currency"]] -= r["amount"]  # Count only the current-account leg.
                if r["amount"] >= 500:
                    withdrawals.append(r)
        rates = [{"currency": c, "net_savings_flow": str(savings_flows[c]), "rate_percent": str(round(savings_flows[c] / incomes[c] * 100, 2))} for c in currencies if incomes[c] > 0]
        if rates:
            put("monthly_savings_rate", rates, confidence=1, limitations=["Net transfers to identified SVGS accounts, not returns or total wealth."])
        put("savings_withdrawal_detected", {"detected": bool(withdrawals), "threshold": "500 units per currency", "transaction_ids": ids(withdrawals)}, ids(withdrawals), 1,
            limitations=["Fixed demo threshold; no inference about the reason for withdrawal."])
        if len(complete) == 3:
            previous = [r for r in external if month_start(r["date"]) in complete[:-1]]
            for key, positive in [("income_change_percentage", True), ("expense_change_percentage", False)]:
                changes = []
                for c in currencies:
                    avg = sum((abs(r["amount"]) for r in previous if r["currency"] == c and (r["amount"] > 0 if positive else r["amount"] < 0)), ZERO) / 2
                    now = incomes[c] if positive else expenses[c]
                    changes.append({"currency": c, "current": str(now), "previous_average": str(avg), "percentage": percent(now, avg)})
                put(key, changes, ids(previous + current), 1, limitations=["Latest complete month compared with the previous two-month average; percentage is undefined for a zero baseline."])
            housing = {"rent", "energy", "furniture"}
            house_changes, distributions = [], []
            for c in currencies:
                old = [r for r in previous if r["currency"] == c and r["amount"] < 0]
                new = [r for r in current if r["currency"] == c and r["amount"] < 0]
                old_house = sum((-r["amount"] for r in old if r["category"] in housing), ZERO) / 2
                new_house = sum((-r["amount"] for r in new if r["category"] in housing), ZERO)
                house_changes.append({"currency": c, "current": str(new_house), "previous_average": str(old_house), "percentage": percent(new_house, old_house)})
                for category in sorted({r["category"] for r in old + new}):
                    old_total, new_total = -sum((r["amount"] for r in old), ZERO), -sum((r["amount"] for r in new), ZERO)
                    if old_total and new_total:
                        a = -sum((r["amount"] for r in old if r["category"] == category), ZERO) / old_total * 100
                        b = -sum((r["amount"] for r in new if r["category"] == category), ZERO) / new_total * 100
                        distributions.append({"currency": c, "category": category, "previous_share": str(round(a, 2)), "current_share": str(round(b, 2)), "change_points": str(round(b-a, 2))})
            put("housing_spending_increase", house_changes, ids(previous + current), .75,
                limitations=["Exact calculation using partly text-based categories; classification quality is separate from arithmetic accuracy."])
            put("spending_category_shift", distributions, ids(previous + current), .75)

    travel = [r for r in recent if r["amount"] < 0 and r["category"] in {"flight", "hotel"}]
    put("travel_spending_detected", bool(travel), ids(travel), .65, status="inferred",
        limitations=["Merchant labels do not prove the customer is personally travelling."])
    foreign = [r for r in recent if r["amount"] < 0 and (r["currency"] != "EUR" or (r["payload"].get("merchantCountry") and r["payload"]["merchantCountry"] != "BE"))]
    if any(r["payload"].get("merchantCountry") or r["currency"] != "EUR" for r in recent):
        put("foreign_transaction_activity", bool(foreign), ids(foreign), 1,
            limitations=["Demo reference: Belgium/EUR. Merchant country or currency does not prove physical travel."])
    anomalies = []
    enough_baseline = False
    for r in recent:
        if r["amount"] >= 0:
            continue
        baseline = [float(abs(p["amount"])) for p in external if p["date"] <= cutoff and p["currency"] == r["currency"] and p["amount"] < 0 and p["category"] == r["category"]]
        if len(baseline) >= 5:
            enough_baseline = True
            avg, std = mean(baseline), pstdev(baseline)
            amount = float(abs(r["amount"]))
            z = (amount - avg) / std if std else None
            if amount > avg * 2 and amount - avg >= 100 and (z is None or z >= 3):
                anomalies.append({"transaction_id": r["id"], "currency": r["currency"], "amount": str(abs(r["amount"])), "z_score": round(z, 2) if z is not None else None})
    if enough_baseline:
        put("unusual_spending", anomalies, [a["transaction_id"] for a in anomalies], .7, status="inferred",
            limitations=["At least 5 reference expenses per category; >2x average, +100 units and z>=3 for non-zero variance. This is not a fraud probability."])

    histories = list(db.scalars(select(BalanceObservation).where(BalanceObservation.account_id.in_([a.resource_id for a in eligible]), BalanceObservation.reference_date <= as_of))) if "balances" in access["effective_permissions"] else []
    account_currencies = {a.resource_id: a.details["currency"] for a in eligible}
    histories = [h for h in histories if h.payload["balanceAmount"]["currency"] == account_currencies[h.account_id]]
    selected_balances = {}
    for a in eligible:
        candidates = [b for b in balances if b.account_id == a.resource_id and b.balance_type in {"interimBooked", "closingBooked"} and 0 <= (as_of - date.fromisoformat(b.payload["referenceDate"])).days <= 7]
        if candidates:
            selected_balances[a.resource_id] = max(candidates, key=lambda b: (b.payload["referenceDate"], b.balance_type == "interimBooked"))
    for account_type, key in [("CACC", "current_account_balance"), ("SVGS", "savings_balance")]:
        target = [a for a in eligible if a.details.get("cashAccountType") == account_type]
        if target and all(a.resource_id in selected_balances for a in target):
            totals = defaultdict(Decimal)
            for a in target:
                totals[a.details["currency"]] += D(selected_balances[a.resource_id].payload["balanceAmount"]["amount"])
            put(key, [{"currency": c, "amount": str(v), "balance_type": "booked"} for c, v in sorted(totals.items())], confidence=1,
                limitations=["Authorised private accounts only; booked snapshots less than 8 days old."])
    compute_history(catalog, histories, eligible, as_of, months)
    compute_forecast(catalog, selected_balances, eligible, patterns, as_of, covered)
    compute_products_usage(catalog, context, as_of)
    responses = list(db.scalars(select(CategoryFeedback).where(CategoryFeedback.customer_id == customer_id)))
    events = list(db.scalars(select(Insight).where(Insight.customer_id == customer_id)))
    put("customer_confirmed_event", [{"category": r.category, "status": r.status, "date": str(r.observed_at)} for r in responses if r.valid_until >= today()] +
        [{"event": e.type, "status": "confirmed" if e.status == "confirmed" else "rejected"} for e in events if e.status in {"confirmed", "dismissed"}],
        confidence=1, source="kate_confirmation", status="confirmed")
    categories = categorize(catalog, analysis, responses, as_of)
    return {"signals": catalog, "categories": categories, "synthetic": bool(context and context.synthetic), "blocked": False}


def compute_history(catalog, histories, accounts, as_of, months):
    daily = {}
    for h in histories:
        if h.balance_type not in {"interimBooked", "closingBooked"}:
            continue
        key = (h.account_id, h.reference_date)
        if key not in daily or h.balance_type == "interimBooked":
            daily[key] = h
    savings = [a for a in accounts if a.details.get("cashAccountType") == "SVGS"]
    if savings:
        monthly = []
        for month in months:
            totals = defaultdict(Decimal)
            for a in savings:
                candidates = [h for (aid, day), h in daily.items() if aid == a.resource_id and 0 <= (month_end(month) - day).days <= 7]
                if not candidates:
                    break
                h = max(candidates, key=lambda h: h.reference_date)
                totals[a.details["currency"]] += D(h.payload["balanceAmount"]["amount"])
            else:
                monthly.append(totals)
        if len(monthly) == 3:
            values = []
            for c, before in monthly[0].items():
                after = monthly[-1][c]
                delta = after - before
                margin = max(D(10), abs(before) * D('.02'))
                values.append({"currency": c, "direction": "increasing" if delta > margin else "decreasing" if delta < -margin else "stable", "change": str(delta)})
            set_signal(catalog, "savings_trend", values, limitations=["Three recent monthly snapshots per account; stability threshold of 2% or 10 units."])
    overdrafts = []
    for a in accounts:
        if a.details.get("cashAccountType") != "CACC":
            continue
        days = [as_of - timedelta(days=n) for n in reversed(range(30))]
        if not all((a.resource_id, day) in daily for day in days):
            continue
        negative = [D(daily[(a.resource_id, d)].payload["balanceAmount"]["amount"]) < 0 for d in days]
        overdrafts.append({"account_id": a.resource_id, "negative_days": sum(negative),
                           "episodes": sum(v and (i == 0 or not negative[i-1]) for i, v in enumerate(negative))})
    current = [a for a in accounts if a.details.get("cashAccountType") == "CACC"]
    if current and len(overdrafts) == len(current):
        set_signal(catalog, "overdraft_frequency", overdrafts,
                   limitations=["30 consecutive days of booked balances; a period starting negative counts as an observed episode."])


def compute_forecast(catalog, balances, accounts, patterns, as_of, covered):
    if not covered(as_of - timedelta(days=89), as_of):
        return
    forecasts = []
    for account in accounts:
        balance = balances.get(account.resource_id)
        if account.details.get("cashAccountType") != "CACC" or not balance or (as_of - date.fromisoformat(balance.payload["referenceDate"])).days > 1:
            continue
        local = [g for g in patterns if g[-1]["account"].resource_id == account.resource_id]
        income = [g for g in local if g[-1]["amount"] > 0]
        expenses = [g for g in local if g[-1]["amount"] < 0]
        future_income = [shift_month(g[-1]["date"], 1) for g in income]
        if not expenses or not future_income or any(d <= as_of for d in future_income):
            continue
        until = min(future_income)
        if (until - as_of).days > 35:
            continue
        projected = D(balance.payload["balanceAmount"]["amount"])
        for group in expenses:
            due = shift_month(group[-1]["date"], 1)
            if as_of < due < until:
                projected += sum((r["amount"] for r in group), ZERO) / len(group)
        forecasts.append({"currency": account.details["currency"], "until": str(until), "projected_booked_balance": str(round(projected, 2)), "risk": projected < 0})
    if forecasts:
        set_signal(catalog, "low_balance_risk", forecasts, source="inferred", status="inferred", confidence=.6,
                   limitations=["Indicative forecast using known recurring payments only, excluding unexpected expenses and pending transactions. Not a creditworthiness assessment."])


def compute_products_usage(catalog, context, as_of):
    if not context:
        return
    snapshot = context.products
    if fresh(snapshot, as_of) and snapshot["complete"]:
        products = [p for p in snapshot["items"] if p["status"] == "active"]
        loans = [p for p in products if p["type"] in LOANS]
        for key, value in [("loan_products", sorted({p["type"] for p in loans}) or ["no_loan"]),
                           ("owned_kbc_products", sorted({p["type"] for p in products})),
                           ("mortgage_started", any(p["type"] == "mortgage" and 0 <= (as_of - date.fromisoformat(p["started_at"])).days <= 30 for p in products))]:
            set_signal(catalog, key, value, timestamp=snapshot["observed_at"])
        if all(p.get("monthly_repayment") for p in loans):
            totals = defaultdict(Decimal)
            for p in loans:
                m = p["monthly_repayment"]
                totals[m["currency"]] += D(m["amount"])
            set_signal(catalog, "monthly_loan_commitment", [{"currency": c, "amount": str(v)} for c, v in sorted(totals.items())], timestamp=snapshot["observed_at"])
        investments = [p for p in products if p["type"] == "investments"]
        if all(p.get("valuation") for p in investments):
            totals = defaultdict(Decimal)
            for p in investments:
                m = p["valuation"]
                totals[m["currency"]] += D(m["amount"])
            set_signal(catalog, "investment_balance", [{"currency": c, "amount": str(v)} for c, v in sorted(totals.items())], timestamp=snapshot["observed_at"])
    elif snapshot:
        for key in ("loan_products", "owned_kbc_products", "mortgage_started", "monthly_loan_commitment", "investment_balance"):
            catalog[key].update(availability="unavailable", limitations=["Incomplete, expired or future-dated product snapshot."])
    usage = context.usage
    if usage and date.fromisoformat(usage["to_date"]) <= as_of and (as_of - date.fromisoformat(usage["to_date"])).days <= 30:
        mobile = [s for s in usage["sessions"] if s["channel"] == "mobile"]
        set_signal(catalog, "mobile_usage_frequency", {"sessions": len(mobile), "active_days": len({s["date"] for s in mobile}),
                   "from": usage["from_date"], "to": usage["to_date"]}, timestamp=usage["to_date"])
        set_signal(catalog, "kate_interaction_history", usage["kate_interactions"], timestamp=usage["to_date"])
        counts = Counter(s["channel"] for s in usage["sessions"])
        if catalog["preferred_channel"]["availability"] != "available" and counts:
            top = counts.most_common()
            if len(top) == 1 or top[0][1] > top[1][1]:
                set_signal(catalog, "preferred_channel", top[0][0], source="inferred", confidence=.75, status="inferred", timestamp=usage["to_date"])


def categorize(catalog, analysis, responses, as_of):
    categories = []
    def available(key):
        return catalog[key]["availability"] == "available"
    def value(key):
        return catalog[key]["value"] if available(key) else None
    def category(key, label, evidence, confidence=.75, status="inferred", source="inferred"):
        item = {"key": key, "label": label, "value": True, "confidence": confidence,
                "confidence_kind": "heuristic_not_calibrated" if status == "inferred" else "input_assertion",
                "source": source, "timestamp": str(as_of), "status": status, "evidence": evidence}
        categories.append(item)
    for key in ("age_group", "employment_status", "household_status", "dependents_count", "investment_risk_profile"):
        v = value(key)
        if v is not None and v != "unknown":
            s = catalog[key]
            categories.append({"key": key, "label": s["label"], "value": v, "confidence": s["confidence"],
                               "confidence_kind": s["confidence_kind"], "source": s["source"], "timestamp": s["timestamp"],
                               "status": s["status"], "evidence": [key]})
    work = value("employment_status")
    if work == "student":
        category("student", STATE_LABELS["student"], ["employment_status"], 1, "confirmed", catalog["employment_status"]["source"])
    elif work in (None, "unknown") and value("student_grant_detected") is True:
        category("student", "Possible student profile", ["student_grant_detected"], .65)
    if value("age_group") in {"18-25", "26-35"} and (work in {"employed", "self_employed"} or (work in (None, "unknown") and "salary" in (value("income_source_type") or []))):
        category("young_professional", STATE_LABELS["young_professional"], ["age_group", "employment_status" if work else "income_source_type"], .8)
    if work == "student" and value("new_recurring_salary") is True:
        category("first_job", STATE_LABELS["first_job"], ["employment_status", "new_recurring_salary"], .75)
    active = {e["type"]: e for e in analysis["events"]}
    for event, key in [("MOVING", "moving"), ("TRAVEL", "travelling")]:
        if event in active:
            category(key, STATE_LABELS[key], [s["type"] for s in active[event]["evidence"]], active[event]["score"] / 100)
    if value("mortgage_started") is True:
        category("home_purchase", STATE_LABELS["home_purchase"], ["mortgage_started"], .75)
    risk = value("low_balance_risk") or []
    overdrafts = value("overdraft_frequency") or []
    stopped = value("salary_stopped") or {}
    negative_balance = any(D(b["amount"]) < 0 for b in value("current_account_balance") or [])
    frequent_overdrafts = any(o["episodes"] >= 2 for o in overdrafts)
    if (any(f["risk"] for f in risk) and frequent_overdrafts) or (stopped.get("detected") and (negative_balance or frequent_overdrafts)):
        category("financial_stress", STATE_LABELS["financial_stress"], [k for k in ("low_balance_risk", "salary_stopped", "overdraft_frequency", "current_account_balance") if available(k)], .65)
    if any(v["direction"] == "increasing" for v in value("savings_trend") or []):
        category("wealth_growth", STATE_LABELS["wealth_growth"], ["savings_trend"], .8)
    for response in responses:
        item = next((c for c in categories if c["key"] == response.category), None)
        still_supported = item is not None
        if item and item["source"] != "inferred":
            continue  # Declarations are edited as facts, not overwritten by inferred-state feedback.
        if item is None:
            item = {"key": response.category, "label": STATE_LABELS[response.category], "value": True, "evidence": []}
            categories.append(item)
        expired = response.valid_until < max(today(), as_of) or (not still_supported and response.status == "confirmed" and response.category in {"student", "young_professional"})
        item.update(status="expired" if expired else response.status, source="kate_confirmation", confidence=None if expired else 1,
                    confidence_kind="customer_response", timestamp=str(response.observed_at), valid_until=str(response.valid_until))
    return categories
