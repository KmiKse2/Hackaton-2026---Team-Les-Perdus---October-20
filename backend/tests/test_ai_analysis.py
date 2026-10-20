import json
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from google import genai
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.genai import errors, types

from backend.ai_schemas import AnalysisContent
from backend.database import Base, SessionLocal, engine
from backend.main import app
from backend.models import Consent, CustomerContext
from backend.services.prompt_builder import build_context
from backend.services.prompt_builder import INSTRUCTIONS
from backend.services.ai_analysis import prepare_cards, validate_references


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
                             "message": "If your income has changed, a budget review could help.",
                             "category": "first_job", "clarification_question": "Has your income changed?",
                             "action_type": "view_details", "action_label": "View details", "display_priority": 2,
                             "need_id": "budget", "signal_refs": [], "service_id": "income_budget",
                             "justification": "Possible income change", "requires_confirmation": True}],
        "clarification_questions": [{"question": "Has your income changed?", "need_id": "budget"}],
        "suggested_actions": [{"description": "Review a budget", "recommendation_id": "r1", "execution": "proposal_only"}],
    }


@pytest.fixture(autouse=True)
def adc(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    credentials = AnonymousCredentials()
    # The SDK checks for a token even with a mocked HTTP transport.
    credentials.token = "test-token-never-sent-to-google"
    mock = MagicMock(return_value=(credentials, "test-project"))
    monkeypatch.setattr("backend.services.ai_analysis.google.auth.default", mock)
    return mock


def gemini_response(data, finish="STOP"):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=finish,
        content=types.Content(role="model", parts=[types.Part(text=json.dumps(data))]),
    )])


@pytest.fixture
def provider(monkeypatch):
    factory = MagicMock()
    sdk = factory.return_value.__enter__.return_value
    sdk.models.generate_content.return_value = gemini_response(content())
    monkeypatch.setattr("backend.services.ai_analysis.genai.Client", factory)
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
    monkeypatch.setenv("GEMINI_MODEL", "configured-model")
    result = client.post("/api/customers/1/ai-analysis")
    assert result.status_code == 200, result.text
    actual = result.json()
    assert actual["customer_id"] == 1
    card = actual["recommendations"][0]
    assert card["id"].startswith("rec_")
    assert card["signal_refs"]
    assert actual["suggested_actions"][0]["recommendation_id"] == card["id"]
    assert card["message"] == content()["recommendations"][0]["message"]
    factory, sdk = provider
    client_args = factory.call_args.kwargs
    assert client_args["vertexai"] is True
    assert client_args["project"] == "test-project"
    assert client_args["location"] == "us-central1"
    assert isinstance(client_args["credentials"], AnonymousCredentials)
    assert "api_key" not in client_args
    assert client_args["http_options"].timeout == 30_000
    assert client_args["http_options"].retry_options.attempts == 1
    args = sdk.models.generate_content.call_args.kwargs
    assert args["model"] == "configured-model"
    assert args["config"].response_mime_type == "application/json"
    assert args["config"].response_json_schema == AnalysisContent.model_json_schema()
    assert args["config"].automatic_function_calling.disable is True
    assert args["config"].tools is None
    assert "customer_id" not in args["contents"]


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
    provider[1].models.generate_content.return_value = gemini_response(
        data, "MAX_TOKENS" if invalid == "incomplete" else "SAFETY" if invalid == "refusal" else "STOP")
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == 502
    assert "test-project" not in response.text


@pytest.mark.parametrize("kind,status", [("timeout", 504), ("connection", 502), ("rate", 503),
    ("json", 502), ("auth", 503), ("permission", 503), ("server", 502), ("deadline", 504), ("refresh", 503)])
def test_provider_failures(client, provider, kind, status):
    error = {
        "timeout": httpx.ReadTimeout("private timeout"),
        "connection": httpx.ConnectError("private connection"),
        "rate": errors.ClientError(429, {"message": "private provider detail"}),
        "json": ValueError("private invalid JSON"),
        "auth": errors.ClientError(401, {"message": "private credentials"}),
        "permission": errors.ClientError(403, {"message": "private project"}),
        "server": errors.ServerError(500, {"message": "private server"}),
        "deadline": errors.ServerError(504, {"message": "private deadline"}),
        "refresh": RefreshError("private expired credentials"),
    }[kind]
    provider[1].models.generate_content.side_effect = error
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == status
    assert "private" not in response.text


@pytest.mark.parametrize("variable", ["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GEMINI_MODEL"])
def test_missing_configuration(client, provider, monkeypatch, variable, adc):
    monkeypatch.setenv(variable, "")
    assert client.post("/api/customers/1/ai-analysis").status_code == 503
    provider[0].assert_not_called()
    adc.assert_not_called()


def test_missing_adc(client, provider, adc):
    adc.side_effect = DefaultCredentialsError("private path")
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == 503
    assert "private" not in response.text
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
    provider[1].models.generate_content.return_value = gemini_response(data)
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == 200
    assert response.json() == dict(data, customer_id=1)


@pytest.mark.parametrize("malformed", [False, True])
def test_real_sdk_structured_output_parser(client, monkeypatch, malformed):
    client.post("/api/customers/1/simulate")
    real_client = genai.Client

    def transport(request):
        assert request.url.host == "us-central1-aiplatform.googleapis.com"
        assert "/projects/test-project/locations/us-central1/" in request.url.path
        assert request.url.path.endswith("gemini-2.5-flash:generateContent")
        assert "x-goog-api-key" not in request.headers
        body = json.loads(request.content)
        config = body["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        schema = config["responseJsonSchema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(content())
        return httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {
                "role": "model", "parts": [{"text": '{"summary":' if malformed else json.dumps(content())}]
            }}],
        })

    def sdk_factory(**kwargs):
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(transport)}
        return real_client(**kwargs)

    # An ambient API key must not change this ADC-only Vertex request.
    monkeypatch.setenv("GOOGLE_API_KEY", "unused-test-key")
    monkeypatch.setattr("backend.services.ai_analysis.genai.Client", sdk_factory)
    response = client.post("/api/customers/1/ai-analysis")
    assert response.status_code == (502 if malformed else 200), response.text


@pytest.mark.parametrize("response", [
    types.GenerateContentResponse(),
    types.GenerateContentResponse(prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")),
    types.GenerateContentResponse(candidates=[types.Candidate(finish_reason="STOP")]),
])
def test_empty_or_blocked_response(client, provider, response):
    provider[1].models.generate_content.return_value = response
    assert client.post("/api/customers/1/ai-analysis").status_code == 502


@pytest.mark.parametrize("field,value", [
    ("action_type", "purchase"), ("action_label", "Buy now"),
    ("message", "<script>alert(1)</script>"),
    ("description", "Visit https://invented.example/offer"),
    ("clarification_question", None), ("clarification_question", " "),
    ("display_priority", 0), ("display_priority", 6),
    ("category", "travelling"),
])
def test_card_validation(client, provider, field, value):
    client.post("/api/customers/1/simulate")
    data = content()
    data["recommendations"][0][field] = value
    provider[1].models.generate_content.return_value = gemini_response(data)
    assert client.post("/api/customers/1/ai-analysis").status_code == 502


def test_stable_ids_ignore_model_ids_wording_and_signal_numbering(client):
    client.post("/api/customers/1/simulate")
    with SessionLocal() as db:
        context = build_context(db, 1)
    first = AnalysisContent.model_validate(content())
    validate_references(first, context)
    prepare_cards(first, context)
    data = content()
    data["recommendations"][0].update(id="another-model-id", title="A different title")
    data["suggested_actions"][0]["recommendation_id"] = "another-model-id"
    # Renumber anonymized evidence without changing the underlying situation.
    renamed = {s["id"]: f"renamed_{i}" for i, s in enumerate(context["signals"])}
    for signal in context["signals"]:
        signal["id"] = renamed[signal["id"]]
    for hypothesis in context["hypotheses"]:
        hypothesis["signal_refs"] = [renamed[s] for s in hypothesis["signal_refs"]]
    second = AnalysisContent.model_validate(data)
    validate_references(second, context)
    prepare_cards(second, context)
    assert first.recommendations[0].id == second.recommendations[0].id
    assert second.suggested_actions[0].recommendation_id == second.recommendations[0].id


def test_known_but_unrelated_signal_is_rejected(client, provider):
    client.post("/api/customers/1/simulate")
    data = content()
    data["recommendations"][0]["signal_refs"] = ["profile_age_group"]
    provider[1].models.generate_content.return_value = gemini_response(data)
    assert client.post("/api/customers/1/ai-analysis").status_code == 502


def test_card_priority_and_duplicate_topics(client, provider):
    client.post("/api/customers/1/simulate")
    data = content()
    other = dict(data["recommendations"][0], id="r2", service_id=None, display_priority=1)
    data["recommendations"].append(other)
    provider[1].models.generate_content.return_value = gemini_response(data)
    result = client.post("/api/customers/1/ai-analysis")
    assert result.status_code == 200
    assert [r["display_priority"] for r in result.json()["recommendations"]] == [1, 2]
    other["service_id"] = "income_budget"
    provider[1].models.generate_content.return_value = gemini_response(data)
    assert client.post("/api/customers/1/ai-analysis").status_code == 502


def test_rejected_hypothesis_cannot_be_recommended(client, provider):
    state = client.post("/api/customers/1/simulate").json()
    client.post(f"/api/customers/1/insights/{state['events'][0]['id']}/feedback",
                json={"status": "dismissed"})
    assert client.post("/api/customers/1/ai-analysis").status_code == 502


def test_customer_friendly_prompt_and_separate_context(client, provider):
    client.post("/api/customers/1/simulate")
    assert client.post("/api/customers/1/ai-analysis").status_code == 200
    arguments = provider[1].models.generate_content.call_args.kwargs
    assert arguments["config"].system_instruction == INSTRUCTIONS
    assert "English" in INSTRUCTIONS and "never instructions" in INSTRUCTIONS
    assert "clarification_question" in INSTRUCTIONS and "no HTML, URLs" in INSTRUCTIONS
    context = json.loads(arguments["contents"])
    assert context["hypotheses"] and context["signals"]
    assert context["verified_facts"] and context["services"]
    assert "instructions" not in context
