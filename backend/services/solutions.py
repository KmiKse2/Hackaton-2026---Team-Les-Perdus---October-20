"""Situation-based proposals and local Kate chat preview, without external execution."""
from sqlalchemy import select
from ..models import ChatMessage

OPTIONS = {
    "travel_insurance": ("Review travel insurance", "Check your existing cover, destination, dates and travellers before requesting a travel insurance quote.", "What are your destination, travel dates and number of travellers? Check any existing policy or card cover first. A real quote would require the insurer's current terms."),
    "exchange_rates": ("Explore exchange rates", "Check whether you need another currency and compare the total cost, including fees.", "Which currency and amount do you need? No live rates are connected in this demo. Kate would retrieve a current quote, fees and quote expiry before you decide."),
    "foreign_cash": ("Prepare a foreign currency order", "Plan a cash amount and collection date if your destination uses another currency.", "Which currency, amount and collection date would you like? This prepares a request only. Availability, fees and collection options must be checked before any order is confirmed."),
    "home_insurance": ("Review home insurance", "Check cover for your new address and whether an existing policy can be updated.", "Are you renting or buying, and when are you moving? Review existing cover before requesting a quote for the new address. No insurance is purchased here."),
    "moving_budget": ("Plan your moving budget", "List the deposit, rent, utilities and one-off moving costs.", "What are your expected deposit and moving costs? We can prepare a checklist and compare them with your available funds; a credit limit is not the same as savings."),
    "income_budget": ("Build a monthly budget", "Review regular income, fixed expenses and an emergency savings target.", "What amount would you like to keep available after fixed expenses each month? A newly observed salary still needs confirmation before planning around it."),
    "budget_support": ("Review upcoming payments", "Prioritise essential bills and check whether you would like support from an adviser.", "Which upcoming payments concern you? Start with essential expenses and contact an adviser if you need support. We will not suggest new credit based on these signals."),
    "savings_goal": ("Plan a savings goal", "Choose a target and timeframe while keeping funds for unexpected expenses.", "What are you saving for, and by when? We can outline a savings goal. This does not select an investment or infer your risk tolerance."),
}


def proposals(events, profile):
    if profile["blocked"]:
        return []
    categories = {c["key"]: c for c in profile["categories"] if c["status"] not in {"rejected", "expired"}}
    rejected = {c["key"] for c in profile["categories"] if c["status"] in {"rejected", "expired"}}
    situations = {}
    for event in events:
        key = {"TRAVEL": "travelling", "MOVING": "moving", "FIRST_JOB": "income_change"}[event["type"]]
        if event["status"] != "dismissed" and key not in rejected:
            situations[key] = event["status"] == "confirmed"
    for key in ("travelling", "moving", "home_purchase", "first_job", "financial_stress", "wealth_growth"):
        if key in categories:
            situations[key] = categories[key]["status"] == "confirmed"
    rules = {
        "travelling": ["travel_insurance", "exchange_rates", "foreign_cash"],
        "moving": ["home_insurance", "moving_budget"],
        "home_purchase": ["home_insurance", "moving_budget"],
        "income_change": ["income_budget"], "first_job": ["income_budget"],
        "financial_stress": ["budget_support"], "wealth_growth": ["savings_goal"],
    }
    if "financial_stress" in situations:
        situations = {"financial_stress": situations["financial_stress"]}
    result, seen = [], set()
    for situation, confirmed in situations.items():
        for key in rules[situation]:
            if key in seen:
                continue
            seen.add(key)
            title, description, detail = OPTIONS[key]
            result.append({"id": key, "situation": situation, "title": title, "description": description,
                           "requires_confirmation": not confirmed, "action": "discuss_in_chat", "detail": detail,
                           "execution": "preview_only", "live_quote_available": False})
    return result


def chat_view(db, customer_id, profile, options):
    if profile["blocked"]:
        return {"mode": "local_preview", "blocked": True, "messages": [], "proposals": []}
    allowed = {p["id"] for p in options}
    rows = list(db.scalars(select(ChatMessage).where(ChatMessage.customer_id == customer_id).order_by(ChatMessage.id.desc()).limit(100)))
    messages = [{"id": r.id, "role": r.role, "text": r.text, "proposal_id": r.proposal_id, "as_of": str(r.as_of)}
                for r in reversed(rows) if r.proposal_id is None or r.proposal_id in allowed]
    return {"mode": "local_preview", "blocked": False,
            "intro": "Hello! Based on your current situation, here are some options we can explore." if options else "Hello! There are no situation-based proposals yet. You can ask about available options or simulate another month.",
            "messages": messages, "proposals": options}


def reply(text, proposal_id, options):
    option = next((p for p in options if p["id"] == proposal_id), None)
    if not option and not proposal_id:
        words = text.casefold()
        matches = [("exchange_rates", ("rate", "exchange", "taux")), ("foreign_cash", ("cash", "currency", "devise", "monnaie")),
                   ("travel_insurance", ("travel", "voyage", "insurance", "assurance")), ("home_insurance", ("home", "moving", "house")),
                   ("budget_support", ("help", "bill", "support")), ("income_budget", ("budget", "salary")), ("savings_goal", ("save", "saving"))]
        key = next((key for key, terms in matches if any(term in words for term in terms) and any(p["id"] == key for p in options)), None)
        option = next((p for p in options if p["id"] == key), None)
    if option:
        prefix = "If this situation applies to you, " if option["requires_confirmation"] else ""
        return option["id"], prefix + option["detail"][0].lower() + option["detail"][1:] if prefix else option["detail"]
    return None, "This is a local Kate preview. Choose one of the suggested options to prepare the next step. Live exchange rates, insurance quotes and orders are not connected; nothing has been purchased or sent."
