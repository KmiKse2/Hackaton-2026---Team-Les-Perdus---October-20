"""Stateless OpenAI adapter. No persistence, tools, chat, or action execution."""
import json
import os

from fastapi import HTTPException
from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from pydantic import ValidationError

from ..ai_schemas import AIAnalysis, AnalysisContent
from .prompt_builder import INSTRUCTIONS


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
        uncertain = any(hypotheses[h]["status"] != "confirmed" for h in need.hypothesis_refs)
        uncertain |= any(signals[s]["status"] == "inferred" or signals[s].get("strength") == "weak" for s in need.signal_refs)
        if uncertain and not need.requires_confirmation:
            raise ValueError("Unconfirmed need")
    for recommendation in recommendations.values():
        if recommendation.need_id not in needs or not set(recommendation.signal_refs) <= signals.keys():
            raise ValueError("Unknown recommendation reference")
        if recommendation.service_id is not None and recommendation.service_id not in services:
            raise ValueError("Unknown service")
        uncertain = needs[recommendation.need_id].requires_confirmation
        uncertain |= any(signals[s]["status"] == "inferred" or signals[s].get("strength") == "weak" for s in recommendation.signal_refs)
        if recommendation.service_id:
            uncertain |= services[recommendation.service_id]["requires_confirmation"]
        if uncertain and not recommendation.requires_confirmation:
            raise ValueError("Confirmation required")
    if any(q.need_id is not None and q.need_id not in needs for q in result.clarification_questions):
        raise ValueError("Unknown question need")
    if any(a.recommendation_id not in recommendations for a in result.suggested_actions):
        raise ValueError("Unknown action recommendation")


def analyze_context(customer_id, context):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "AI analysis is not configured")
    try:
        with OpenAI(api_key=key, timeout=30.0, max_retries=0) as client:
            response = client.responses.parse(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                instructions=INSTRUCTIONS,
                input=json.dumps(context, ensure_ascii=False),
                text_format=AnalysisContent,
                max_output_tokens=4000,
                store=False,
            )
        if response.status != "completed" or response.output_parsed is None:
            raise ValueError("Incomplete or refused response")
        result = AnalysisContent.model_validate(response.output_parsed)
        validate_references(result, context)
    except APITimeoutError:
        raise HTTPException(504, "AI analysis timed out") from None
    except RateLimitError:
        raise HTTPException(503, "AI analysis is temporarily unavailable") from None
    except APIError:
        raise HTTPException(502, "AI provider request failed") from None
    except (ValidationError, ValueError):
        raise HTTPException(502, "AI provider returned an invalid analysis") from None
    return AIAnalysis(customer_id=customer_id, **result.model_dump())
