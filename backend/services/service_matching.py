"""Deterministic catalogue matching, adapted from karim.zip's intent/ranking engine.

Only structured categories are interpreted. Ecosystem membership and prose cannot
activate a product. Provider output never supplies destinations or capabilities.
"""
import re
from pydantic import Field

from ..ai_schemas import AIAnalysis, Recommendation
from ..life_schemas import ServiceRecord, Preferences, safe_official_url
from .banking import today
from .ecosystem import catalogue

# Adapted event families from the ZIP, extended for LifeFlow's goal categories.
ALIASES = {
    "moving": "housing", "home_purchase": "housing", "buy_house": "housing",
    "buy_home": "housing", "house": "housing", "home": "housing",
    "home_renovation": "renovation", "renovating": "renovation",
    "car": "mobility", "buy_car": "mobility", "vehicle": "mobility",
    "travelling": "travel", "travel_planning": "travel", "holiday": "travel",
    "entrepreneurship": "business", "business_creation": "business",
    "pension": "retirement", "investment": "investing", "new_job": "first_job",
}


def intent(value):
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return ALIASES.get(token, token)


class MatchedService(ServiceRecord):
    why_recommended: str
    matched_intent: str
    match_score: int
    requires_confirmation: bool
    destination_available: bool
    action_label: str = "Learn more"
    limitation: str = "Optional external information. No purchase, quote or banking action is performed."


class DisplayRecommendation(Recommendation):
    matched_services: list[MatchedService] = Field(default_factory=list, max_length=3)


class DisplayAnalysis(AIAnalysis):
    recommendations: list[DisplayRecommendation] = Field(max_length=5)


def match_services(category, preferences, *, confirmed=False, rejected=(), excluded=(),
                   owned=(), preferred_id=None, records=None):
    """Rank exact structured intents; scores are ordering weights, not suitability."""
    topic = intent(category)
    blocked = {intent(v) for v in [*preferences.protected_categories, *preferences.declined_questions, *rejected]}
    allowed = {intent(v) for v in preferences.allowed_categories}
    if not topic or topic in blocked or (allowed and topic not in allowed):
        return []
    excluded = set(excluded) | set(owned)
    matches = []
    for service in catalogue() if records is None else records:
        s = ServiceRecord.model_validate(service).model_dump(mode="json")
        if (s["id"] in excluded or s["verification_status"] != "official_source"
                or s["last_verified_at"] > str(today())
                or topic not in {intent(c) for c in s["supported_intents"]}):
            continue
        # Never turn a broad ambition into unsolicited credit or investment sales.
        if s["service_kind"] in {"credit", "investment", "technical", "unreviewed"}:
            continue
        if s["service_kind"] == "insurance":
            # An unknown situation or existing coverage needs review, not another policy.
            if not confirmed or preferences.insurance_policies:
                continue
        available = bool(s["destination_status"] == "verified" and s["destination_url"]
                         and s["destination_verified_at"] <= str(today())
                         and safe_official_url(s["destination_url"]))
        prefix = "For your confirmed" if confirmed else "If you confirm this"
        score = (30 if confirmed else 10) + (3 if available else 0) + (2 if s["id"] == preferred_id else 0)
        # Specific tools precede portals; no broad ecosystem fallback.
        score += max(0, 5 - len(s["supported_intents"]))
        matches.append(MatchedService(**s, why_recommended=f"{prefix} {topic.replace('_', ' ')} project: {s['description']}",
            matched_intent=topic, match_score=score, requires_confirmation=not confirmed,
            destination_available=available))
    return sorted(matches, key=lambda m: (-m.match_score, m.id))[:3]


def excluded_services(recorded):
    from .life import suppressed
    return {key.removeprefix("service:") for key, value in recorded.items()
            if key.startswith("service:") and suppressed(value)}


def owned_services(context):
    """Only exact catalogue IDs/names from confirmed ownership, never transactions."""
    known = set()
    for fact in context.get("verified_facts", []):
        if fact.get("type") == "owned_kbc_products" and fact.get("status") == "confirmed":
            values = fact.get("value", [])
            if isinstance(values, list):
                for v in values:
                    if isinstance(v, str):
                        known.add(v.casefold())
                    elif isinstance(v, dict):
                        known.update(str(v[k]).casefold() for k in ("id", "name", "product_name") if v.get(k))
    return {s["id"] for s in catalogue() if s["id"].casefold() in known or s["name"].casefold() in known
            or ("insurance" in known and s["service_kind"] == "insurance")
            or any(r["id"].casefold() in known or r["name"].casefold() in known for r in s["source_records"])}


def enrich_analysis(analysis, context, recorded):
    prefs = Preferences.model_validate(context["preferences"])
    goals = {g["id"]: g for g in context.get("goals", []) if g["status"] == "active"}
    needs = {n.id: n for n in analysis.detected_needs}
    hypotheses = {h["id"]: h for h in context.get("hypotheses", [])}
    cards = []
    for card in analysis.recommendations:
        goal = goals.get(card.goal_id)
        need = needs.get(card.need_id)
        topics = [card.category] if card.category else (need.hypothesis_refs if need else [])
        category = goal["category"] if goal else (topics[0] if len(topics) == 1 else None)
        confirmed = bool(goal) or (not card.requires_confirmation and bool(topics)
                     and all(hypotheses.get(t, {}).get("status") == "confirmed" for t in topics))
        matches = match_services(category, prefs, confirmed=confirmed,
            rejected=context.get("rejected_hypotheses", []), excluded=excluded_services(recorded),
            owned=owned_services(context), preferred_id=card.service_id)
        cards.append(DisplayRecommendation(**card.model_dump(), matched_services=matches))
    return DisplayAnalysis(**analysis.model_dump(exclude={"recommendations"}), recommendations=cards)


def goal_services(items, preferences, recorded=None, owned=()):
    found = {}
    for goal in items:
        if goal["status"] != "active":
            continue
        for s in match_services(goal["category"], preferences, confirmed=True,
                                excluded=excluded_services(recorded or {}), owned=owned):
            found.setdefault(s.id, s.model_dump(mode="json"))
    return list(found.values())[:6]


def conversation_services(output, context, recorded):
    prefs = Preferences.model_validate(context["preferences"])
    # Immediately honour a new restrictive preference; permissive changes await confirmation.
    if output.financing_preference == "saving":
        prefs.financing_preference = "saving"
    categories = [g.category for g in output.goal_updates] or [output.intent]
    found = {}
    for category in categories:
        for s in match_services(category, prefs, confirmed=False,
                rejected=context.get("rejected_hypotheses", []), excluded=excluded_services(recorded),
                owned=owned_services(context)):
            found.setdefault(s.id, s.model_dump(mode="json"))
    return list(found.values())[:3]
