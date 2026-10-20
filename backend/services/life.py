"""Customer-controlled goals, memory and Decimal planning; no financial execution."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING

from fastapi import HTTPException
from sqlalchemy import delete, select

from ..context_schemas import ContextImport
from ..life_schemas import GoalInput, Preferences
from ..models import (ChatMessage, ChatProposal, CustomerContext, LifeDecision,
                      LifeGoal, LifePlan, LifeSettings, RecommendationCache, SubscriptionReview)
from .banking import inspect, require_access, today
from .context import personalization_allowed, update_context
from .customer_state import fresh, prepare_rows, recurring

D = Decimal
ZERO = D(0)
PERSONAL_KEYS = {"housing_status", "region", "personal_constraints", "hobbies", "passions",
                 "interests", "ambitions", "life_priorities", "financial_constraints",
                 "insurance_coverage", "retirement_preferences"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def require_life(db, cid):
    require_access(db, cid, ["accounts", "transactions"])
    if not personalization_allowed(db, cid):
        raise HTTPException(403, "Personalization consent is required")


def settings(db, cid):
    row = db.get(LifeSettings, cid)
    return Preferences.model_validate(row.data if row else {})


def goals(db, cid):
    return [dict(row.data, id=row.id, revision=row.revision, confirmed_by_customer=True,
                 created_at=row.created_at, updated_at=row.updated_at)
            for row in db.scalars(select(LifeGoal).where(LifeGoal.customer_id == cid).order_by(LifeGoal.id))]


def goal_row(db, cid, gid):
    row = db.scalar(select(LifeGoal).where(LifeGoal.customer_id == cid, LifeGoal.id == gid))
    if row is None:
        raise HTTPException(404, "Goal not found")
    return row


def validate_allocations(items, prefs):
    budgets = {b.currency: b for b in prefs.budgets}
    for currency in {g["currency"] for g in items}:
        total = sum((D(g["allocated_savings"]) for g in items if g["currency"] == currency), ZERO)
        budget = budgets.get(currency)
        if total and (budget is None or budget.savings_pool is None):
            raise HTTPException(422, "Declare your available savings pool and reserve before allocating savings")
        if budget and budget.savings_pool is not None and total > max(ZERO, budget.savings_pool - budget.emergency_reserve):
            raise HTTPException(422, "Allocated savings exceed the shared pool after the emergency reserve")
    if sum(bool(g.get("primary")) for g in items if g["status"] == "active") > 1:
        raise HTTPException(422, "Only one active goal can be primary")


def invalidate(db, cid):
    db.execute(delete(RecommendationCache).where(RecommendationCache.customer_id == cid))


def save_goal(db, cid, payload, gid=None):
    require_life(db, cid)
    row = goal_row(db, cid, gid) if gid else None
    revision = getattr(payload, "expected_revision", None)
    if row and revision is not None and revision != row.revision:
        raise HTTPException(409, "Goal changed; review the latest version")
    data = GoalInput.model_validate(payload.model_dump(exclude={"expected_revision"})).model_dump(mode="json")
    items = [g for g in goals(db, cid) if g["id"] != gid] + [data]
    if len(items) > 50:
        raise HTTPException(422, "A maximum of 50 goals is supported")
    validate_allocations(items, settings(db, cid))
    if row:
        row.data, row.revision, row.updated_at = data, row.revision + 1, now()
    else:
        row = LifeGoal(customer_id=cid, data=data, revision=1, created_at=now(), updated_at=now())
        db.add(row)
    db.flush()
    invalidate(db, cid)
    persist_plan(db, cid)
    return row


def save_settings(db, cid, payload):
    require_life(db, cid)
    validate_allocations(goals(db, cid), payload)
    row = db.get(LifeSettings, cid)
    if row is None:
        row = LifeSettings(customer_id=cid)
        db.add(row)
    row.data = payload.model_dump(mode="json")
    if not payload.memory_enabled:
        db.execute(delete(ChatMessage).where(ChatMessage.customer_id == cid))
        db.execute(delete(ChatProposal).where(ChatProposal.customer_id == cid))
    db.flush()
    invalidate(db, cid)
    persist_plan(db, cid)


def calculate_plan(items, prefs, as_of):
    """Zero-return scenarios. Allocations remain reserved even for paused goals."""
    budgets = {b.currency: b for b in prefs.budgets}
    plan = {"as_of": str(as_of), "basis": "customer_declared_zero_return_simulation",
            "assumptions": ["No interest, investment returns, inflation, tax or fees assumed.",
                            "Savings allocations are bookkeeping only, not bank transfers.",
                            "Property price is not the required deposit; confirm your own funding target."],
            "goals": [], "budgets": [], "warnings": []}
    for currency in sorted(set(budgets) | {g["currency"] for g in items}):
        budget = budgets.get(currency)
        allocated = sum((D(g["allocated_savings"]) for g in items if g["currency"] == currency), ZERO)
        committed = sum((D(g["monthly_contribution"]) for g in items if g["currency"] == currency and g["status"] == "active"), ZERO)
        capacity = budget.monthly_capacity if budget else None
        pool = budget.savings_pool if budget else None
        reserve = budget.emergency_reserve if budget else ZERO
        plan["budgets"].append({"currency": currency, "allocated": str(allocated),
            "monthly_committed": str(committed), "monthly_capacity": str(capacity) if capacity is not None else None,
            "unallocated": str(max(ZERO, pool - reserve - allocated)) if pool is not None else None,
            "emergency_reserve": str(reserve),
            "monthly_gap": str(max(ZERO, committed - capacity)) if capacity is not None else None})
        if capacity is not None and committed > capacity:
            plan["warnings"].append(f"{currency}: contributions exceed capacity; prioritise goals or extend deadlines.")
        if pool is not None and pool < reserve:
            plan["warnings"].append(f"{currency}: the declared emergency reserve is not yet funded.")
    # Priority allocation uses capacity once across all active goals, never once per goal.
    remaining_capacity = {c: b.monthly_capacity for c, b in budgets.items()}
    for g in sorted(items, key=lambda g: (not g.get("primary", False), g["priority"], g.get("id", 0))):
        target = D(g["target_amount"]) if g["target_amount"] is not None else None
        allocated, contribution = D(g["allocated_savings"]), D(g["monthly_contribution"])
        remaining = max(ZERO, target - allocated) if target is not None else None
        deadline = date.fromisoformat(g["target_date"]) if g["target_date"] else None
        months = max(0, (deadline.year - as_of.year) * 12 + deadline.month - as_of.month +
                     (1 if deadline.day > as_of.day else 0)) if deadline else None
        required = (remaining / months).quantize(D(".01"), rounding=ROUND_CEILING) if remaining is not None and months else None
        cap = remaining_capacity.get(g["currency"])
        priority_amount = None
        if cap is not None and g["status"] == "active":
            priority_amount = min(cap, required if required is not None else contribution)
            remaining_capacity[g["currency"]] = max(ZERO, cap - priority_amount)
        scenarios = []
        if remaining is not None and contribution > 0:
            scenarios.append({"type": "extend_deadline", "months": int((remaining / contribution).to_integral_value(rounding=ROUND_CEILING)),
                              "monthly": str(contribution)})
        if target is not None and months is not None:
            scenarios.append({"type": "adjust_target", "target": str((allocated + contribution * months).quantize(D(".01")))})
        if priority_amount is not None:
            scenarios.append({"type": "priority_allocation", "monthly": str(priority_amount),
                              "note": "Uses shared capacity once, in priority order; no allocation is executed."})
        if prefs.budget_optimization and required is not None and required > contribution:
            scenarios.append({"type": "voluntary_expense_review", "note": "Review only categories you choose; potential savings are not income."})
        plan["goals"].append({"goal_id": g.get("id"), "title": g["title"], "currency": g["currency"],
            "remaining": str(remaining) if remaining is not None else None, "months_available": months,
            "monthly_required": str(required) if required is not None else None,
            "monthly_gap": str(max(ZERO, required - contribution)) if required is not None else None,
            "progress_percent": float(min(D(100), allocated / target * 100)) if target and target > 0 else None,
            "status": ("non_financial" if g["amount_basis"] == "non_financial" else
                       "clarify_deposit" if g["amount_basis"] == "property_price" else
                       "achieved" if remaining == 0 else "overdue" if months == 0 else
                       "missing_information" if required is None else "on_track" if contribution >= required else "gap"),
            "scenarios": scenarios})
    return plan


def persist_plan(db, cid):
    plan = calculate_plan(goals(db, cid), settings(db, cid), today())
    row = db.get(LifePlan, cid)
    if row is None:
        row = LifePlan(customer_id=cid)
        db.add(row)
    row.data = plan
    db.flush()
    return plan


def stress_scenario(db, cid, payload):
    require_life(db, cid)
    prefs = settings(db, cid).model_copy(deep=True)
    budget = next((b for b in prefs.budgets if b.currency == payload.currency), None)
    if budget is None or budget.savings_pool is None or budget.monthly_capacity is None:
        raise HTTPException(422, "Declare savings and monthly capacity in this currency before simulating")
    budget.savings_pool = max(ZERO, budget.savings_pool - payload.unexpected_expense)
    budget.monthly_capacity = max(ZERO, budget.monthly_capacity - payload.monthly_capacity_reduction)
    items = goals(db, cid)
    for g in items:
        if g["currency"] == payload.currency and g["target_amount"] and g["target_date"]:
            deadline = date.fromisoformat(g["target_date"])
            # Explicit hypothetical cost growth, never a promised investment return.
            years = min(100, max(0, deadline.year - today().year))
            g["target_amount"] = str((D(g["target_amount"]) * (1 + payload.annual_cost_growth_percent / 100) ** years).quantize(D(".01")))
    result = calculate_plan(items, prefs, today())
    result["scenario_inputs"] = payload.model_dump(mode="json")
    result["assumptions"].append("Customer-entered stress assumptions, not forecasts. Confirmed goals and budgets are unchanged.")
    allocated = sum((D(g["allocated_savings"]) for g in items if g["currency"] == payload.currency), ZERO)
    if allocated > max(ZERO, budget.savings_pool - budget.emergency_reserve):
        result["warnings"].append("The hypothetical expense leaves insufficient funds for the reserve and existing allocations. Review priorities.")
    base = persist_plan(db, cid)
    row = db.get(LifePlan, cid)
    row.data = dict(base, stress_scenario=result)
    db.flush()
    return result


def coverage_checks(prefs):
    result = []
    for index, left in enumerate(prefs.insurance_policies):
        for right in prefs.insurance_policies[index + 1:]:
            risks = {r.casefold().strip() for r in left.risks} & {r.casefold().strip() for r in right.risks}
            start = max(left.start_date, right.start_date, today())
            end = min(left.end_date, right.end_date)
            if left.scope.casefold().strip() == right.scope.casefold().strip() and risks and start <= end:
                result.append({"policies": [left.label, right.label], "shared_risks": sorted(risks),
                               "overlap_from": str(start), "overlap_until": str(end),
                               "status": "possible_overlap_requires_policy_review",
                               "message": "Declared coverage may overlap. Check exclusions, limits and beneficiaries with your insurer before changing anything."})
    return result


def structured_memory(db, cid):
    row = db.get(CustomerContext, cid)
    return {k: v for k, v in (row.attributes if row else {}).items() if fresh(v, today())}


def memory_revision(db, cid):
    return digest({"facts": structured_memory(db, cid), "goals": goals(db, cid),
                   "preferences": settings(db, cid).model_dump(mode="json")})


def subscriptions(db, cid):
    analysis, _, accounts, _ = inspect(db, cid)
    rows = prepare_rows(db, accounts, date.fromisoformat(analysis["as_of"]))
    patterns = recurring(rows)
    # Preserve the established grouping rule. A newly changed price may fall
    # outside its 20% tolerance; retain an already established monthly group
    # when its next payment arrives on schedule, for the review feature only.
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        if r["identity"] and not r["counterpart"] and r["amount"] < 0:
            grouped[(r["account"].resource_id, r["currency"], r["identity"])].append(r)
    existing = {(g[-1]["account"].resource_id, g[-1]["currency"], g[-1]["identity"]) for g in patterns}
    for key, group in grouped.items():
        if key not in existing and len(group) >= 3 and recurring(group[:-1]) and 25 <= (group[-1]["date"] - group[-2]["date"]).days <= 35:
            patterns.append(group[-3:])
        elif key not in existing and len(group) >= 2 and 330 <= (group[-1]["date"] - group[-2]["date"]).days <= 400:
            patterns.append(group[-2:])
    reviews = {r.key: r.data for r in db.scalars(select(SubscriptionReview).where(SubscriptionReview.customer_id == cid))}
    result = []
    for group in patterns:
        last = group[-1]
        if last["amount"] >= 0:
            continue
        key = digest([last["account"].resource_id, last["currency"], last["identity"]])
        review = reviews.get(key, {})
        amount = abs(last["amount"])
        confirmed = review.get("confirmed", False)
        frequency = review.get("frequency", "unknown")
        detected_frequency = "annual" if (group[-1]["date"] - group[-2]["date"]).days >= 330 else "monthly"
        annual = amount * (12 if frequency == "monthly" else 1) if confirmed and frequency != "unknown" else None
        previous = abs(group[-2]["amount"])
        increase = ((amount - previous) / previous * 100).quantize(D(".01")) if previous else ZERO
        result.append(dict(id=key, merchant=(last["payload"].get("creditorName") or "Recurring payment")[:100],
            category=last["category"], amount=str(amount), currency=last["currency"],
            detected_frequency=detected_frequency, frequency=frequency, confirmed=confirmed,
            estimated_annual_cost=str(annual) if annual is not None else None,
            projected_annual_cost=str(amount * (12 if detected_frequency == "monthly" else 1)), monthly_cost=str(annual / 12) if annual is not None else None,
            last_payment_date=str(last["date"]), increase_percent=str(increase), **{
                k: review.get(k, default) for k, default in
                [("usage_status", "unknown"), ("importance", "unknown"), ("customer_decision", "undecided"),
                 ("last_reviewed_at", None), ("renewal_date", None), ("goal_id", None)]},
            potential_annual_saving=str(annual) if annual is not None and review.get("usage_status") == "unused"
                and review.get("customer_decision") == "consider_cancelling" else None,
            note="Possible recurring expense, not proof of a subscription or its usefulness. No cancellation is performed."))
    return result


def save_review(db, cid, key, payload):
    require_life(db, cid)
    item = next((s for s in subscriptions(db, cid) if s["id"] == key), None)
    if item is None:
        raise HTTPException(404, "Recurring payment not found in the authorised accounts")
    if payload.goal_id:
        target = goal_row(db, cid, payload.goal_id)
        if target.data["currency"] != item["currency"]:
            raise HTTPException(422, "Subscription and goal currencies must match")
    row = db.scalar(select(SubscriptionReview).where(SubscriptionReview.customer_id == cid, SubscriptionReview.key == key))
    if row is None:
        row = SubscriptionReview(customer_id=cid, key=key)
        db.add(row)
    row.data = dict(payload.model_dump(mode="json"), last_reviewed_at=now())
    db.flush()
    invalidate(db, cid)


def decisions(db, cid):
    return {r.key: r.data for r in db.scalars(select(LifeDecision).where(LifeDecision.customer_id == cid))}


def suppressed(data):
    return data.get("status") in {"rejected", "dismissed", "accepted"} or (
        data.get("status") == "postponed" and data.get("until", "") > str(today()))


def save_decision(db, cid, key, payload):
    row = db.scalar(select(LifeDecision).where(LifeDecision.customer_id == cid, LifeDecision.key == key))
    if row is None:
        row = LifeDecision(customer_id=cid, key=key)
        db.add(row)
    row.data = {"status": payload.status, "until": str(today() + timedelta(days=payload.postpone_days)), "updated_at": now()}
    db.flush()


def notifications(db, cid, items, plan, subs, profile):
    prefs = settings(db, cid)
    if not prefs.notifications or prefs.proactivity == "off":
        return []
    found = []
    def add(key, category, message, goal_id=None):
        if prefs.allowed_categories and category not in prefs.allowed_categories:
            return
        if category in prefs.protected_categories or category in prefs.declined_questions:
            return
        found.append({"id": key, "category": category, "message": message, "goal_id": goal_id,
                      "action": "view_details", "delivery": "in_app"})
    for goal, p in ((g, next(p for p in plan["goals"] if p["goal_id"] == g["id"])) for g in items if g["status"] == "active"):
        if p["status"] in {"achieved", "overdue", "gap"}:
            add(f"goal-{goal['id']}-{p['status']}", goal["category"],
                {"achieved": "Your declared savings have reached this goal's target.",
                 "overdue": "This goal's deadline has passed. Would you like to review it?",
                 "gap": "Your planned monthly contribution is below the amount needed. Review the scenarios."}[p["status"]], goal["id"])
        elif p["status"] in {"missing_information", "clarify_deposit"}:
            add(f"goal-{goal['id']}-clarify", goal["category"], "Complete this goal's budget or deadline to refine its plan.", goal["id"])
        else:
            add(f"goal-{goal['id']}-created", goal["category"], "Your goal is saved. Review its next step when you are ready.", goal["id"])
    for sub in subs:
        if sub["usage_status"] == "do_not_ask":
            continue
        if prefs.budget_optimization and not sub["confirmed"]:
            add("sub-" + sub["id"][:24], "subscriptions", "A possible monthly payment is ready for your review.")
        elif prefs.budget_optimization and sub["confirmed"] and sub["usage_status"] == "unknown":
            add("confirmed-sub-" + sub["id"][:24], "subscriptions", "You confirmed a recurring payment. Would you like to record how useful it is?")
        if sub["confirmed"] and D(sub["increase_percent"]) >= prefs.subscription_increase_percent:
            add("increase-" + sub["id"][:24] + "-" + sub["amount"], "subscriptions", "A recurring payment increased. Would you like to review it?")
        if prefs.reminders and sub["renewal_date"] and 0 <= (date.fromisoformat(sub["renewal_date"]) - today()).days <= prefs.renewal_notice_days:
            add("renew-" + sub["id"][:24] + sub["renewal_date"], "subscriptions", "A renewal date you supplied is approaching. Check your existing cover and terms.")
    for category in profile.get("categories", []):
        if category["status"] not in {"rejected", "expired"} and category["key"] in {"first_job", "wealth_growth", "moving", "financial_stress"}:
            add("situation-" + category["key"] + "-" + category["status"], category["key"],
                "Your financial situation may have changed. Would you like to review your goals?" if category["status"] != "confirmed"
                else "You confirmed a change in your situation. Would you like to review your goals?")
    income_change = profile.get("signals", {}).get("income_change_percentage", {})
    if income_change.get("availability") == "available":
        for change in income_change.get("value", []):
            if change.get("percentage") is not None and abs(D(change["percentage"])) >= prefs.income_change_percent:
                add("income-" + digest(change)[:24], "income", "Observed income changed compared with prior months. Confirm whether this is lasting before adjusting your goals.")
    memory = structured_memory(db, cid)
    cover = memory.get("insurance_coverage", {}).get("value")
    if any(g["category"] in {"housing", "travel", "mobility", "family"} for g in items):
        add("insurance-review", "insurance", "Review existing insurance before adding cover. Compare insured risks, people and dates; overlapping cover is not automatically a duplicate."
            if cover else "Do you already have suitable insurance? Check existing cover before considering another policy.")
    for policy in prefs.insurance_policies if prefs.reminders else []:
        if 0 <= (policy.end_date - today()).days <= prefs.renewal_notice_days:
            add("policy-" + digest(policy.model_dump(mode="json"))[:24], "insurance",
                "A declared policy end date is approaching. Check renewal terms and existing cover before deciding.")
    recorded = decisions(db, cid)
    found = [n for n in found if not suppressed(recorded.get(n["id"], {}))]
    # Persist delivery timestamps only for the selected batch, to avoid repeated alerts.
    recent = [v for v in recorded.values() if v.get("delivered_at") and
              (datetime.now(timezone.utc) - datetime.fromisoformat(v["delivered_at"])).total_seconds() < prefs.recommendation_interval_hours * 3600]
    shown = [n for n in found if recorded.get(n["id"], {}).get("delivered_at")]
    if not recent:
        for n in [n for n in found if not recorded.get(n["id"], {}).get("delivered_at")][:1 if prefs.proactivity == "low" else 3]:
            row = db.scalar(select(LifeDecision).where(LifeDecision.customer_id == cid, LifeDecision.key == n["id"]))
            if row is None:
                row = LifeDecision(customer_id=cid, key=n["id"])
                db.add(row)
            row.data = {"status": "shown", "delivered_at": now()}
            shown.append(n)
        db.flush()
    return shown


def life_view(db, cid, profile=None):
    # Review/deletion of personal data remains possible after consent withdrawal.
    allowed = personalization_allowed(db, cid)
    try:
        require_access(db, cid, ["accounts", "transactions"])
    except HTTPException:
        allowed = False
    prefs = settings(db, cid)
    items = goals(db, cid)
    plan = calculate_plan(items, prefs, today())
    saved = db.get(LifePlan, cid)
    if saved and saved.data.get("stress_scenario"):
        plan["stress_scenario"] = saved.data["stress_scenario"]
    pending = [{"id": r.id, **r.data, "created_at": r.created_at} for r in
               db.scalars(select(ChatProposal).where(ChatProposal.customer_id == cid, ChatProposal.status == "pending"))] if allowed else []
    from .service_matching import goal_services, excluded_services, owned_services
    owned = owned_services({"verified_facts": [
        dict((profile or {}).get("signals", {}).get("owned_kbc_products", {}), type="owned_kbc_products")
    ]}) if allowed else set()
    subs = subscriptions(db, cid) if allowed else []
    estimates = []
    if allowed and profile:
        income = profile.get("signals", {}).get("monthly_income", {})
        expenses = profile.get("signals", {}).get("monthly_expenses", {})
        if income.get("availability") == expenses.get("availability") == "available" and isinstance(income.get("value"), list):
            for entry in income["value"]:
                cost = next((e for e in expenses.get("value", []) if e["currency"] == entry["currency"]), None)
                if cost:
                    estimates.append({"currency": entry["currency"], "amount": str(D(entry["amount"]) - D(cost["amount"])),
                                      "basis": "indicative_observed_income_less_expenses_not_guaranteed_capacity"})
    return {"blocked": not allowed, "goals": items, "plan": plan,
            "preferences": prefs.model_dump(mode="json"),
            "coverage_checks": coverage_checks(prefs),
            "estimated_monthly_surplus": estimates,
            "information": (db.get(CustomerContext, cid).attributes if db.get(CustomerContext, cid) else {}),
            "pending_proposals": pending, "services": goal_services(items, prefs, decisions(db, cid), owned) if allowed else [],
            "dismissed_service_ids": sorted(excluded_services(decisions(db, cid))) if allowed else [],
            "subscriptions": subs,
            "notifications": notifications(db, cid, items, plan, subs, profile or {}) if allowed else [],
            "catalogue_note": "Official information only; no partner API or live offer is connected."}


def erase_memory(db, cid):
    row = db.get(CustomerContext, cid)
    if row:
        row.attributes = {}
        row.usage = None
    for model in (ChatMessage, ChatProposal, RecommendationCache):
        db.execute(delete(model).where(model.customer_id == cid))
    db.flush()
