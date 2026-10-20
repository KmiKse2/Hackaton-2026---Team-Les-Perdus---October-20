"""Existing chat's Gemini workflow, using the shared transport and confirmed memory."""
from datetime import date

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from ..context_schemas import ContextImport
from ..life_schemas import ConversationOutput, GoalInput
from ..models import ChatMessage, ChatProposal, LifeSettings
from . import ai_analysis
from .banking import today
from .context import update_context
from .life import (goals, goal_row, invalidate, memory_revision, now, require_life,
                  save_goal, save_settings, settings, structured_memory)
from .prompt_builder import CORE_INSTRUCTIONS, build_context

CHAT_INSTRUCTIONS = CORE_INSTRUCTIONS + """
This request is a conversation in the existing LifeFlow assistant.
Interpret the current user_message against confirmed facts, goals and the latest
conversation. Ask goal-specific questions (retirement timing and existing pension
savings for retirement; location/budget/time/deposit for housing; budget and timing
for a race), never a generic questionnaire. A hobby alone is not a request for credit.
Propose a goal only for a customer-declared ambition, with unknown values null.
Use goal_id only to update a supplied goal. Preserve existing values unless corrected.
Set related_goal_ids to the supplied active goals actually discussed in this turn,
even when no goal update is needed. Leave it empty for unrelated conversation.
For a project discussion, use its category as intent, not generic labels such as
goal or planning. Do not create a goal update just to obtain service suggestions.
Use concise standard categories where appropriate: housing, renovation, energy,
mobility, retirement, savings, travel, education, business, family, hobby, debt,
personal. Other categories are supported. Do not invent amounts or deadlines.
For a supplied time horizon convert relative years/months to a calendar date using
today, but do not calculate financial projections; these come from financial_plan.
memory_updates propose concise customer-stated facts, never inferred assumptions.
Preference to avoid loans is financing_preference=saving. Otherwise null unless
explicitly discussed. All updates require an explicit review/confirmation step.
Do not claim the goal or fact has been saved. Explain that proposed changes need
confirmation. If memory is disabled, no proposals may be generated.
Use only official_source ecosystem service IDs from the current context.
Respond with the exact JSON schema. Output 0–3 questions separately; avoid
duplicating them in message. Use next_best_action=none if no action is useful.
Do not give personalised investment recommendations; offer an adviser review.
"""


def converse(db, cid, message):
    require_life(db, cid)
    context = build_context(db, cid)
    prefs = settings(db, cid)
    # Reuse the existing history, bounded and only within the current consent scope.
    history = list(db.scalars(select(ChatMessage).where(ChatMessage.customer_id == cid).order_by(ChatMessage.id.desc()).limit(12))) if prefs.memory_enabled else []
    context.update(today=str(today()), user_message=message,
                   conversation=[{"role": m.role, "text": m.text} for m in reversed(history)])
    valid_services = {s["id"] for s in context["services"] if s.get("verification_status") == "official_source"}
    schema = ConversationOutput.model_json_schema()
    ids = [g["id"] for g in context["goals"]]
    if ids:
        schema["properties"]["related_goal_ids"]["items"]["enum"] = ids
    else:
        schema["properties"]["related_goal_ids"]["maxItems"] = 0
    schema["$defs"]["GoalProposal"]["properties"]["goal_id"]["anyOf"] = (
        [{"type": "integer", "enum": ids}] if ids else []) + [{"type": "null"}]
    if valid_services:
        schema["properties"]["ecosystem_service_ids"]["items"]["enum"] = sorted(valid_services)
    else:
        schema["properties"]["ecosystem_service_ids"]["maxItems"] = 0
    if not prefs.memory_enabled:
        for field in ("goal_updates", "memory_updates"):
            schema["properties"][field]["maxItems"] = 0
        schema["properties"]["financing_preference"]["anyOf"] = [{"type": "null"}]
    output = ai_analysis.generate_structured(context, schema, ConversationOutput, CHAT_INSTRUCTIONS)
    if not set(output.ecosystem_service_ids) <= valid_services:
        raise HTTPException(502, "AI returned an unknown service")
    if not set(output.related_goal_ids) <= set(ids):
        raise HTTPException(502, "AI returned an unknown goal")
    if not prefs.memory_enabled and (output.goal_updates or output.memory_updates or output.financing_preference):
        raise HTTPException(502, "AI proposed memory changes while memory is disabled")
    proposed = []
    try:
        for suggestion in output.goal_updates:
            old = goal_row(db, cid, suggestion.goal_id).data if suggestion.goal_id else {}
            data = dict(old, **suggestion.model_dump(exclude={"goal_id"}))
            valid = GoalInput.model_validate(data)
            proposed.append({"kind": "goal", "goal_id": suggestion.goal_id, "value": valid.model_dump(mode="json")})
        proposed.extend({"kind": "memory", "key": m.key, "value": m.value} for m in output.memory_updates)
        if output.financing_preference:
            proposed.append({"kind": "preference", "value": output.financing_preference})
    except (ValidationError, HTTPException):
        raise HTTPException(502, "AI proposed invalid goal information") from None
    revision = memory_revision(db, cid)
    if proposed:
        db.add(ChatProposal(customer_id=cid, data={"changes": proposed, "expected_context": revision,
                                                  "source": "unconfirmed_gemini_proposal"}, created_at=now(), status="pending"))
    text = output.message
    if output.clarification_questions:
        text += "\n\n" + "\n".join(output.clarification_questions)
    if prefs.memory_enabled:
        for role, value in (("user", message), ("assistant", text)):
            db.add(ChatMessage(customer_id=cid, role=role, text=value[:3000], proposal_id=None, as_of=today()))
    db.flush()
    from .service_matching import conversation_services
    from .life import decisions
    return dict(output.model_dump(), ephemeral=not prefs.memory_enabled, displayed_text=text,
                matched_services=conversation_services(output, context, decisions(db, cid)))


def confirm(db, cid, proposal_id, accept):
    require_life(db, cid)
    row = db.scalar(select(ChatProposal).where(ChatProposal.customer_id == cid, ChatProposal.id == proposal_id))
    if row is None:
        raise HTTPException(404, "Proposal not found")
    if row.status != "pending":
        raise HTTPException(409, "Proposal already reviewed")
    if not accept:
        row.status = "rejected"
        return
    if not settings(db, cid).memory_enabled:
        raise HTTPException(403, "Memory is disabled")
    if row.data["expected_context"] != memory_revision(db, cid):
        raise HTTPException(409, "Your information changed. Ask the assistant to prepare an updated proposal.")
    # All changes are one DB transaction. A failed allocation rolls everything back.
    for change in row.data["changes"]:
        if change["kind"] == "goal":
            save_goal(db, cid, GoalInput.model_validate(change["value"]), change["goal_id"])
        elif change["kind"] == "memory":
            update_context(db, cid, ContextImport.model_validate({"attributes": {
                change["key"]: {"value": change["value"], "source": "declared", "observed_at": str(today())}}}))
        elif change["kind"] == "preference":
            prefs = settings(db, cid)
            prefs.financing_preference = change["value"]
            save_settings(db, cid, prefs)
    row.status = "accepted"
    invalidate(db, cid)
    db.flush()
