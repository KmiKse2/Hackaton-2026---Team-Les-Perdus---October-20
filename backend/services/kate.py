"""A local handoff contract. No Kate endpoint or credentials are invented."""

COPY = {
    "FIRST_JOB": {"label": "Possible employment change", "title": "Is your income changing?", "message": "A salary and commuting expenses appear in the observed period. Has your work situation changed?", "action": "Plan my budget", "tip": "List your fixed expenses before adjusting your budget."},
    "MOVING": {"label": "Possible move", "title": "A new home?", "message": "New rent, furniture and energy expenses appear together. Have you moved?", "action": "Plan my move", "tip": "Review your address, contracts and housing budget."},
    "TRAVEL": {"label": "Possible trip", "title": "Planning a trip?", "message": "Payments suggest flights and accommodation. Are you planning a trip?", "action": "Plan my trip", "tip": "Check your card conditions at your destination and prepare a budget."},
}


def personalize(event):
    return dict(COPY[event["type"]], source="local_preview_for_kate")


def handoff(analysis, events):
    """Only structured observations and hypothesis state; no names/IBAN/free text."""
    rejected = {e["type"] for e in events if e["status"] == "dismissed"}
    hypotheses = [{"type": e["type"], "score": e["score"], "status": e["status"],
                   "requires_confirmation": e["status"] != "confirmed",
                   "signal_ids": [s["id"] for s in e["evidence"]]}
                  for e in events if e["status"] != "dismissed"]
    return {
        "schema_version": "1.0", "integration": "contract_only", "sent_to_kate": False,
        "as_of": analysis["as_of"], "status": "blocked" if analysis["blocked"] else "ready",
        "observations": [{key: s[key] for key in ("id", "type", "label", "strength", "metrics", "limitations", "value", "confidence", "confidence_kind", "source", "timestamp", "status")}
                         for s in analysis["signals"]],
        "hypotheses": hypotheses, "rejected_hypotheses": sorted(rejected),
        "data_quality": analysis["quality"],
        "instructions": ["Scores are rule weights, not probabilities.",
                         "Do not reintroduce a hypothesis the customer has dismissed.",
                         "An observed salary proves neither a first job nor recurring income.",
                         "Interface messages are local previews, not responses from Kate."],
    }
