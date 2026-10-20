import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from backend.ai_schemas import AnalysisContent
from backend.database import Base, SessionLocal, engine
from backend.main import app
from backend.models import Consent, CustomerContext
from backend.services.prompt_builder import build_context


@pytest.fixture
def client():
    assert "lifeflow-pytest-" in str(engine.url.database)
    Base.metadata.drop_all(engine)
    with TestClient(app) as client:
        yield client


def content():
    return {
        "summary": "A possible income change needs confirmation.",
        "detected_needs": [{"id": "budget", "description": "Review the budget",
                            "hypothesis_refs": ["first_job"], "signal_refs": [],
                            "requires_confirmation": True}],
        "recommendations": [{"id": "r1", "title": "Review income", "description": "Plan a budget",
                             "need_id": "budget", "signal_refs": [], "service_id": "income_budget",
                             "justification": "Possible income change", "requires_confirmation": True}],
        "clarification_questions": [{"question": "Has your income changed?", "need_id": "budget"}],
        "suggested_actions": [{"description": "Review a budget", "recommendation_id": "r1", "execution": "proposal_only"}],
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    factory = MagicMock()
    sdk = factory.return_value.__enter__.return_value
    sdk.responses.parse.return_value = SimpleNamespace(status="completed", output_parsed=content())
    monkeypatch.setattr("backend.services.ai_analysis.OpenAI", factory)
    return factory, sdk


def test_prompt_minimizes_data_and_preserves_evidence(client):
    client.post("/api/customers/1/simulate")
    with SessionLocal() as db:
        context = build_context(db, 1)
    wire = json.dumps(context)
    for excluded in ("Thomas", "demo-1", "account_id", "transaction_id", "iban", "date_of_birth", "occupation", "merchant"):
        assert excluded not in wire
    assert context["verified_facts"]
    assert context["financial_summary"]["income"] is not None
    assert context["hypotheses"][0]["signal_refs"]
    assert all(ref.startswith("signal_") for ref in context["hypotheses"][0]["signal_refs"])
    assert "catalogue_status" in context


def test_incomplete_profile_and_balance_permission(client):
    with SessionLocal() as db:
        row = db.get(CustomerContext, 1)
        row.attributes = {}
        consent = db.get(Consent, 1)
        consent.payload = dict(consent.payload, permissions=["accounts", "transactions"])
        db.commit()
        context = build_context(db, 1)
    assert "employment_status" in context["missing_information"]
    assert context["verified_facts"] == []
    assert context["financial_summary"]["balance"] is None
    assert "current_account_balance" in context["missing_information"]


def test_rejected_hypotheses_and_services(client):
    state = client.post("/api/customers/1/simulate").json()
    event = state["events"][0]["id"]
    client.post(f"/api/customers/1/insights/{event}/feedback", json={"status": "dismissed"})
    with SessionLocal() as db:
        context = build_context(db, 1)
    assert "first_job" in context["rejected_hypotheses"]
    assert all(h["id"] != "first_job" for h in context["hypotheses"])
    assert all(s["id"] != "income_budget" for s in context["services"])


@pytest.mark.parametrize("restriction", ["personalization", "expired_personalization", "revoked", "expired", "transactions"])
def test_consent_blocks_before_provider(client, provider, restriction):
    with SessionLocal() as db:
        if restriction in {"personalization", "expired_personalization"}:
            row = db.get(CustomerContext, 1)
            row.personalization = dict(row.personalization, value=restriction != "personalization",
                                       valid_until="2000-01-01" if restriction.startswith("expired") else "2099-01-01")
        else:
            row = db.get(Consent, 1)
            changes = {"consentStatus": "revoked"} if restriction == "revoked" else (
                {"validUntil": "2000-01-01"} if restriction == "expired" else {"permissions": ["accounts"]})
            row.payload = dict(row.payload, **changes)
        db.commit()
    assert client.post("/api/customers/1/ai-analysis").status_code == 403
    provider[0].assert_not_called()


def test_auth_unknown_customer_and_scope(client, provider, monkeypatch):
    monkeypatch.setenv("DEMO_API_TOKEN", "secret")
    assert client.post("/api/customers/1/ai-analysis").status_code == 401
    headers = {"X-Demo-Token": "secret"}
    assert client.post("/api/customers/999/ai-analysis", headers=headers).status_code == 404
    client.post("/api/customers/1/simulate", headers=headers)
    with SessionLocal() as db:
        consent = db.get(Consent, 1)
        # Even corrupted scope cannot expose another customer's accounts.
        consent.account_ids = ["demo-2-current"]
        db.commit()
        context = build_context(db, 1)
    assert context["financial_summary"]["income"] is None
    assert not context["hypotheses"] or all(h["id"] != "first_job" for h in context["hypotheses"])
    provider[0].assert_not_called()


def test_success_and_sdk_arguments(client, provider, monkeypatch):
    client.post("/api/customers/1/simulate")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    result = client.post("/api/customers/1/ai-analysis")
    assert result.status_code == 200, result.text
    assert result.json() == dict(content(), customer_id=1)
    factory, sdk = provider
    assert factory.call_args.kwargs["timeout"] == 30.0
    assert factory.call_args.kwargs["max_retries"] == 0
    args = sdk.responses.parse.call_args.kwargs
    assert args["model"] == "configured-model"
    assert args["store"] is False
    assert args["text_format"] is AnalysisContent
    assert "customer_id" not in args["input"]
    assert "tools" not in args and "previous_response_id" not in args


@pytest.mark.parametrize("invalid", ["schema", "duplicate", "service", "signal", "hypothesis", "confirmation", "action", "refusal", "incomplete"])
def test_invalid_output(client, provider, invalid):
    client.post("/api/customers/1/simulate")
    data = content()
    if invalid == "schema":
        data["unexpected"] = True
    elif invalid == "duplicate":
        data["recommendations"] *= 2
    elif invalid in {"service", "signal"}:
        data["recommendations"][0]["service_id" if invalid == "service" else "signal_refs"] = "invented" if invalid == "service" else ["invented"]
    elif invalid == "hypothesis":
        data["detected_needs"][0]["hypothesis_refs"] = ["rejected"]
    elif invalid == "confirmation":
        data["detected_needs"][0]["requires_confirmation"] = False
    elif invalid == "action":
        data["suggested_actions"][0]["execution"] = "executed"
    provider[1].responses.parse.return_value = SimpleNamespace(
        status="incomplete" if invalid == "incomplete" else "completed",
        output_parsed=None if invalid == "refusal" else data,
    )
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == 502
    assert "test-not-a-real-key" not in response.text


@pytest.mark.parametrize("kind,status", [("timeout", 504), ("connection", 502), ("rate", 503), ("json", 502)])
def test_provider_failures(client, provider, kind, status):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = {
        "timeout": APITimeoutError(request=request),
        "connection": APIConnectionError(request=request),
        "rate": RateLimitError("private provider detail", response=httpx.Response(429, request=request), body=None),
        "json": ValueError("private invalid JSON"),
    }[kind]
    provider[1].responses.parse.side_effect = error
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == status
    assert "private" not in response.text


def test_missing_key(client, provider, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    assert client.post("/api/customers/1/ai-analysis").status_code == 503
    provider[0].assert_not_called()


def test_confirmed_hypothesis_and_account_scope(client):
    state = client.post("/api/customers/1/simulate").json()
    event = state["events"][0]["id"]
    client.post(f"/api/customers/1/insights/{event}/feedback", json={"status": "confirmed"})
    with SessionLocal() as db:
        context = build_context(db, 1)
        assert next(h for h in context["hypotheses"] if h["id"] == "first_job")["status"] == "confirmed"
        consent = db.get(Consent, 1)
        consent.account_ids = ["demo-1-savings"]
        db.commit()
        context = build_context(db, 1)
    assert all(h["id"] != "first_job" for h in context["hypotheses"])
    assert all(s["type"] not in {"new_salary", "commute"} for s in context["signals"])
    assert context["customer_feedback"] == [{"hypothesis": "first_job", "status": "confirmed"}]


def test_empty_analysis_is_valid(client, provider):
    data = dict(summary="Insufficient evidence for personalized recommendations.",
                detected_needs=[], recommendations=[], clarification_questions=[], suggested_actions=[])
    provider[1].responses.parse.return_value = SimpleNamespace(status="completed", output_parsed=data)
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == 200
    assert response.json() == dict(data, customer_id=1)


@pytest.mark.parametrize("malformed", [False, True])
def test_real_sdk_structured_output_parser(client, monkeypatch, malformed):
    client.post("/api/customers/1/simulate")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def transport(request):
        body = json.loads(request.content)
        assert body["store"] is False
        format = body["text"]["format"]
        assert format["type"] == "json_schema"
        assert format["strict"] is True
        assert format["schema"]["additionalProperties"] is False
        assert set(format["schema"]["required"]) == set(content())
        return httpx.Response(200, json={
            "id": "resp_mock", "object": "response", "created_at": 0,
            "model": "gpt-4.1-mini", "status": "completed",
            "output": [{"id": "msg_mock", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{
                            "type": "output_text", "annotations": [],
                            "text": '{"summary":' if malformed else json.dumps(content()),
                        }]}],
        })

    def sdk_factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

    monkeypatch.setattr("backend.services.ai_analysis.OpenAI", sdk_factory)
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == (502 if malformed else 200), response.text
