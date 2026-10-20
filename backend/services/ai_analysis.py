"""Stateless Vertex AI adapter using ADC. No chat or action execution."""
import json
import os
import hashlib
import logging

from fastapi import HTTPException
import google.auth
from google.auth.exceptions import GoogleAuthError
from google import genai
from google.genai import errors, types
import httpx
from pydantic import ValidationError

from ..ai_schemas import AIAnalysis, AnalysisContent
from .prompt_builder import INSTRUCTIONS

logger = logging.getLogger(__name__)


def response_schema(context):
    """Constrain provider references to the IDs actually supplied in this request."""
    schema = AnalysisContent.model_json_schema()
    signals = sorted(s["id"] for s in context["signals"])
    hypotheses = sorted(h["id"] for h in context["hypotheses"])
    services = sorted(s["id"] for s in context["services"])
    definitions = schema["$defs"]
    for model, field, values in (
        ("Need", "signal_refs", signals),
        ("Need", "hypothesis_refs", hypotheses),
        ("Recommendation", "signal_refs", signals),
    ):
        target = definitions[model]["properties"][field]
        if values:
            target["items"]["enum"] = values
        else:
            target["maxItems"] = 0
    for field, values in (("service_id", services), ("category", hypotheses)):
        target = definitions["Recommendation"]["properties"][field]
        target["anyOf"] = ([{"type": "string", "enum": values}] if values else []) + [{"type": "null"}]
    # Mechanical display metadata is resolved locally from the validated need.
    # Do not ask the model to repeat the same references consistently twice.
    card_fields = definitions["Recommendation"]["properties"]
    card_fields["signal_refs"]["maxItems"] = 0
    card_fields["category"]["anyOf"] = [{"type": "null"}]
    # Suggestions always request customer approval; this is conservative even
    # when the underlying observation has already been confirmed.
    for model in ("Need", "Recommendation"):
        definitions[model]["properties"]["requires_confirmation"]["enum"] = [True]
    card_fields["clarification_question"] = {"type": "string", "minLength": 1, "maxLength": 300}
    return schema

def validate_references(result, context):
    signals = {s["id"]: s for s in context["signals"]}
    hypotheses = {h["id"]: h for h in context["hypotheses"]}
    services = {s["id"]: s for s in context["services"]}
    needs = {n.id: n for n in result.detected_needs}
    recommendations = {r.id: r for r in result.recommendations}
    if len(needs) != len(result.detected_needs) or len(recommendations) != len(result.recommendations):
        raise ValueError("Duplicate identifiers")
    for need in needs.values():
        if not need.signal_refs and not need.hypothesis_refs:
            raise ValueError("Unsupported need")
        if not set(need.signal_refs) <= signals.keys() or not set(need.hypothesis_refs) <= hypotheses.keys():
            raise ValueError("Unknown evidence")
        if set(need.hypothesis_refs) & set(context["rejected_hypotheses"]):
            raise ValueError("Rejected hypothesis")
        uncertain = any(hypotheses[h]["status"] != "confirmed" for h in need.hypothesis_refs)
        uncertain |= any(signals[s]["status"] == "inferred" or signals[s].get("strength") == "weak" for s in need.signal_refs)
        if uncertain and not need.requires_confirmation:
            raise ValueError("Unconfirmed need")
    for recommendation in recommendations.values():
        if recommendation.need_id not in needs or not set(recommendation.signal_refs) <= signals.keys():
            raise ValueError("Unknown recommendation reference")
        if recommendation.service_id is not None and recommendation.service_id not in services:
            raise ValueError("Unknown service")
        need = needs[recommendation.need_id]
        if recommendation.category is not None and recommendation.category not in need.hypothesis_refs:
            raise ValueError("Unsupported category")
        evidence = set(need.signal_refs)
        for hypothesis in need.hypothesis_refs:
            evidence.update(hypotheses[hypothesis]["signal_refs"])
        if not set(recommendation.signal_refs) <= evidence:
            raise ValueError("Recommendation evidence does not support its need")
        # Resolve omitted references from the need's already validated evidence.
        if not recommendation.signal_refs:
            recommendation.signal_refs = sorted(evidence)
        if not recommendation.signal_refs:
            raise ValueError("Recommendation has no supporting signals")
        uncertain = needs[recommendation.need_id].requires_confirmation
        uncertain |= any(signals[s]["status"] == "inferred" or signals[s].get("strength") == "weak" for s in recommendation.signal_refs)
        if recommendation.service_id:
            uncertain |= services[recommendation.service_id]["requires_confirmation"]
        if uncertain and not recommendation.requires_confirmation:
            raise ValueError("Confirmation required")
        if recommendation.requires_confirmation and not (recommendation.clarification_question or "").strip():
            raise ValueError("Clarification required")
    if any(q.need_id is not None and q.need_id not in needs for q in result.clarification_questions):
        raise ValueError("Unknown question need")
    if any(a.recommendation_id not in recommendations for a in result.suggested_actions):
        raise ValueError("Unknown action recommendation")


def prepare_cards(result, context):
    """Stable per-topic IDs independent of generated wording and response order."""
    signals = {s["id"]: s["type"] for s in context["signals"]}
    needs = {n.id: n for n in result.detected_needs}
    replacements, seen = {}, set()
    for card in result.recommendations:
        if card.category is None and len(needs[card.need_id].hypothesis_refs) == 1:
            card.category = needs[card.need_id].hypothesis_refs[0]
        topic = {
            "hypotheses": sorted(needs[card.need_id].hypothesis_refs),
            # A hypothesis-backed topic survives changes in supporting evidence.
            "signals": [] if needs[card.need_id].hypothesis_refs else sorted({signals[s] for s in card.signal_refs}),
            "service": card.service_id,
        }
        stable_id = "rec_" + hashlib.sha256(json.dumps(topic, sort_keys=True).encode()).hexdigest()[:20]
        if stable_id in seen:
            raise ValueError("Duplicate recommendation topic")
        seen.add(stable_id)
        replacements[card.id] = stable_id
        card.id = stable_id
    for action in result.suggested_actions:
        action.recommendation_id = replacements[action.recommendation_id]
    result.recommendations.sort(key=lambda r: (r.display_priority, r.id))
    return result


def analyze_context(customer_id, context):
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not project or not location or not model:
        raise HTTPException(503, "AI analysis is not configured")
    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        with genai.Client(
            vertexai=True, project=project, location=location, credentials=credentials,
            http_options=types.HttpOptions(
                api_version="v1", timeout=30_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ) as client:
            response = client.models.generate_content(
                model=model,
                contents=json.dumps(context, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=INSTRUCTIONS,
                    response_mime_type="application/json",
                    response_json_schema=response_schema(context),
                    # Thinking shares the output allowance on Gemini 2.5 Flash.
                    # Bound it so it cannot consume the budget needed for JSON.
                    max_output_tokens=8192,
                    thinking_config=(
                        types.ThinkingConfig(thinking_budget=512)
                        if model == "gemini-2.5-flash" else None
                    ),
                    candidate_count=1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        if (not response.candidates or len(response.candidates) != 1
                or response.candidates[0].finish_reason != types.FinishReason.STOP
                or (response.prompt_feedback and response.prompt_feedback.block_reason)
                or not response.text):
            logger.warning("AI response incomplete: finish_reasons=%s",
                           [str(c.finish_reason) for c in response.candidates or []])
            raise ValueError("Incomplete or refused response")
        result = AnalysisContent.model_validate_json(response.text)
        try:
            validate_references(result, context)
            result = prepare_cards(result, context)
        except ValueError as exc:
            # These validators emit only fixed reason strings, never model text.
            logger.warning("AI reference validation failed: %s", exc)
            raise
    except httpx.TimeoutException:
        raise HTTPException(504, "AI analysis timed out") from None
    except GoogleAuthError:
        raise HTTPException(503, "AI credentials are unavailable; configure Google Cloud ADC") from None
    except errors.APIError as exc:
        if exc.code in {408, 504}:
            raise HTTPException(504, "AI analysis timed out") from None
        if exc.code in {401, 403}:
            raise HTTPException(503, "AI provider authentication or permission failed") from None
        if exc.code in {429, 503}:
            raise HTTPException(503, "AI analysis is temporarily unavailable") from None
        raise HTTPException(502, "AI provider request failed") from None
    except httpx.TransportError:
        raise HTTPException(502, "AI provider request failed") from None
    except ValidationError as exc:
        logger.warning("AI schema validation failed: %s",
                       [(e["loc"], e["type"]) for e in exc.errors()])
        raise HTTPException(502, "AI provider returned an invalid analysis") from None
    except ValueError:
        raise HTTPException(502, "AI provider returned an invalid analysis") from None
    return AIAnalysis(customer_id=customer_id, **result.model_dump())
