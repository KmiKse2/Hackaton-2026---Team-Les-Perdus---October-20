from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.ai_schemas import AIAnalysis, AnalysisContent
from backend.database import SessionLocal
from backend.life_schemas import Preferences, ServiceRecord
from backend.services.ecosystem import catalogue
from backend.services.prompt_builder import build_context
from backend.services.service_matching import (
    enrich_analysis, match_services, owned_services, conversation_services,
)
from backend.tests.test_api import client
from backend.tests.test_ai_analysis import content
from backend.tests.test_life import goal, chat_response, mock_chat


@pytest.mark.parametrize("category,expected", [
    ("HOUSING", "kbc_myhome"), ("home_purchase", "immoscoop"),
    ("CAR", "kbc_mymobility"), ("vehicle", "kbc-036"),
    ("RENOVATION", "setle"), ("HOME_RENOVATION", "setle"),
    ("travelling", "kbc-125"), ("business", "start_it_kbc"),
    ("retirement", "kbc_pension_information"),
])
def test_structured_intent_matching(category, expected):
    matches = match_services(category, Preferences(), confirmed=True)
    assert expected in {s.id for s in matches}
    assert len(matches) <= 3
    assert all(s.why_recommended and s.verification_status == "official_source" for s in matches)
    assert all(s.service_kind not in {"credit", "investment", "technical"} for s in matches)


@pytest.mark.parametrize("category", ["unknown_goal", "cycling_race", "I might rent a car for travel", "", "family"])
def test_no_prose_or_ecosystem_fallback(category):
    assert match_services(category, Preferences(), preferred_id="kbc_myhome") == []


def test_uncertainty_rejection_and_preferences():
    uncertain = match_services("renovation", Preferences())
    assert uncertain and all(s.requires_confirmation and s.why_recommended.startswith("If") for s in uncertain)
    assert not match_services("MOVING", Preferences(), rejected=["housing"])
    assert not match_services("car", Preferences(protected_categories=["mobility"]))
    assert not match_services("housing", Preferences(allowed_categories=["travel"]))
    assert not match_services("travel", Preferences(declined_questions=["travel"]))
    for pref in ("saving", "unspecified", "compare"):
        matches = match_services("housing", Preferences(financing_preference=pref), confirmed=True)
        assert all(s.service_kind != "credit" for s in matches)
    assert not match_services("investing", Preferences(), confirmed=True)


def test_owned_and_covered_insurance_not_duplicated():
    prefs = Preferences(insurance_policies=[dict(label="Existing cover", scope="travel",
        risks=["cancellation"], start_date="2026-01-01", end_date="2027-01-01")])
    assert all(s.service_kind != "insurance" for s in match_services("travel", prefs, confirmed=True))
    owned = owned_services({"verified_facts": [dict(type="owned_kbc_products", status="confirmed", value=["insurance"])]})
    assert "kbc_travel_cover_information" in owned
    assert all(s.id not in owned for s in match_services("travel", Preferences(), confirmed=True, owned=owned))
    assert not owned_services({"verified_facts": [dict(type="owned_kbc_products", status="inferred", value=["insurance"])]})


def test_catalogue_provenance_uniqueness_and_navigation():
    records = catalogue()
    assert len(records) == len({s["id"] for s in records})
    refs = [r["id"] for s in records for r in s["source_records"]]
    assert len(refs) == len(set(refs)) == 229
    assert all(s["integration_status"] == "not_connected" for s in records)
    unverified = [s for s in records if s["verification_status"] == "catalogue_only"]
    assert unverified and all(s["destination_url"] is None for s in unverified)
    s = deepcopy(next(s for s in records if s["id"] == "kbc_myhome"))
    s.update(destination_url=None, destination_status="unverified", destination_verified_at=None)
    matches = match_services("housing", Preferences(), records=[s])
    assert matches and not matches[0].destination_available
    assert matches[0].destination_url is None
    assert not match_services("housing", Preferences(), records=unverified)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "http://www.kbc.be/page",
    "https://www.kbc.be.evil.test/page", "https://www.kbc.be@evil.test/",
    "https://user@www.kbc.be/", "https://www.kbc.be:8443/",
    "https://www.kbc.be/redirect?url=https://evil.test",
    "https://www.kbc.be/page#private", "https://www.kbc.be\\@evil.test/",
])
def test_unsafe_destinations_rejected(url):
    s = deepcopy(catalogue()[0])
    s["destination_url"] = url
    with pytest.raises(ValidationError):
        ServiceRecord.model_validate(s)


def test_enrichment_is_not_in_gemini_schema_and_empty_is_valid():
    assert "matched_services" not in AnalysisContent.model_json_schema()["$defs"]["Recommendation"]["properties"]
    data = content()
    data["recommendations"][0]["category"] = "RENOVATION"
    analysis = AIAnalysis(customer_id=1, **data)
    ctx = dict(preferences=Preferences().model_dump(), goals=[], hypotheses=[], rejected_hypotheses=[])
    enriched = enrich_analysis(analysis, ctx, {})
    assert enriched.recommendations[0].matched_services
    assert enriched.recommendations[0].model_dump(exclude={"matched_services"}) == analysis.recommendations[0].model_dump()
    analysis.recommendations = []
    assert enrich_analysis(analysis, ctx, {}).recommendations == []


def test_analysis_endpoint_enrichment_cache_dismissal_and_consent(client, monkeypatch):
    result = goal(client, category="housing").json()
    gid = result["life"]["goals"][0]["id"]
    data = content()
    data["recommendations"][0].update(goal_id=gid, category=None, service_id=None)
    analysis = AIAnalysis(customer_id=1, **data)
    calls = []
    def generate(cid, context):
        calls.append(cid)
        return analysis.model_copy(deep=True)
    monkeypatch.setattr("backend.main.analyze_context", generate)
    first = client.post("/api/customers/1/ai-analysis")
    assert first.status_code == 200, first.text
    card = first.json()["recommendations"][0]
    assert card["goal_id"] == gid and card["matched_services"]
    assert client.post("/api/customers/1/ai-analysis").json() == first.json()
    assert len(calls) == 1
    sid = card["matched_services"][0]["id"]
    assert client.post(f"/api/customers/1/services/{sid}/feedback", json={"status":"dismissed"}).status_code == 200
    assert sid not in {s["id"] for s in client.get("/api/customers/1/life").json()["services"]}
    assert sid not in {s["id"] for s in client.post("/api/customers/1/ai-analysis").json()["recommendations"][0]["matched_services"]}
    assert sid not in client.get("/api/customers/2/life").json()["dismissed_service_ids"]
    with SessionLocal() as db:
        assert sid not in {s["id"] for s in build_context(db, 1)["services"]}
    assert client.post("/api/customers/1/services/invented/feedback", json={"status":"dismissed"}).status_code == 404
    client.post("/api/customers/1/personalization-consent", json={"value":False,"source":"declared","observed_at":"2026-10-20"})
    assert client.post("/api/customers/1/ai-analysis").status_code == 403
    assert client.post(f"/api/customers/1/services/{sid}/feedback", json={"status":"dismissed"}).status_code == 403
    assert client.get("/api/customers/1/life").json()["services"] == []


def test_conversation_new_goal_confirmation_and_services(client, monkeypatch):
    output = chat_response()
    from backend.life_schemas import GoalProposal
    output.goal_updates = [GoalProposal(goal_id=None, title="A home", category="housing",
        description="A home project", motivation="", target_amount=None, target_date=None,
        currency="EUR", amount_basis="property_deposit")]
    output.financing_preference = "saving"
    mock_chat(monkeypatch, output)
    r = client.post("/api/customers/1/chat", json={"message":"I want a home. I prefer saving."})
    assert r.status_code == 200, r.text
    value = r.json()
    assert not value["life"]["goals"]
    services = value["chat"]["last_response"]["matched_services"]
    assert services and all(s["requires_confirmation"] for s in services)
    assert all(s["service_kind"] != "credit" for s in services)
    proposal = value["life"]["pending_proposals"][0]["id"]
    confirmed = client.post(f"/api/customers/1/proposals/{proposal}/confirm", json={"accept":True}).json()
    assert confirmed["life"]["services"]
    assert confirmed["life"]["preferences"]["financing_preference"] == "saving"
    output.ecosystem_service_ids = ["invented"]
    assert client.post("/api/customers/1/chat", json={"message":"Continue"}).status_code == 502


def test_unknown_preferred_service_never_authoritative():
    matches = match_services("travel", Preferences(), preferred_id="invented")
    assert matches and all(s.id != "invented" for s in matches)
    output = chat_response()
    output.intent = "travel"
    result = conversation_services(output, {"preferences":Preferences().model_dump()}, {})
    assert result and all(s["requires_confirmation"] for s in result)


def test_chat_followup_uses_goal_reference_without_mutation(client, monkeypatch):
    gid = goal(client, category="housing").json()["life"]["goals"][0]["id"]
    output = chat_response()
    output.related_goal_ids = [gid]
    mock_chat(monkeypatch, output)
    r = client.post("/api/customers/1/chat", json={"message":"What can help with my existing home project?"})
    assert r.status_code == 200, r.text
    services = r.json()["chat"]["last_response"]["matched_services"]
    assert services and all(s["matched_intent"] == "housing" for s in services)
    assert all(not s["requires_confirmation"] for s in services)
    assert not r.json()["life"]["pending_proposals"]
    output.related_goal_ids = [999999]
    assert client.post("/api/customers/1/chat", json={"message":"Continue"}).status_code == 502


def test_chat_selected_service_survives_generic_intent_and_respects_dismissal():
    output = chat_response()
    output.ecosystem_service_ids = ["kbc_myhome"]
    context = {"preferences": Preferences().model_dump(), "services": catalogue()}
    matches = conversation_services(output, context, {})
    assert [s["id"] for s in matches] == ["kbc_myhome"]
    assert not conversation_services(output, context, {"service:kbc_myhome": {"status":"dismissed"}})
    assert not conversation_services(output, dict(context, services=[]), {})
    context["preferences"]["protected_categories"] = ["housing", "renovation", "energy"]
    assert not conversation_services(output, context, {})


def test_unrelated_chat_does_not_reuse_all_existing_goals():
    output = chat_response()
    output.intent = "greeting"
    context = {"preferences":Preferences().model_dump(),
               "goals":[{"id":1,"category":"housing","status":"active"}]}
    assert not conversation_services(output, context, {})


def test_explicit_car_insurance_goal_with_default_category(client):
    r = client.post("/api/customers/1/goals", json={"title":"car insurance"})
    assert r.status_code == 200, r.text
    services = r.json()["life"]["services"]
    assert [s["id"] for s in services] == ["kbc-030"]
    assert services[0]["destination_available"]
    assert r.json()["life"]["goals"][0]["category"] == "personal"
    with SessionLocal() as db:
        ctx = build_context(db, 1)
    assert "kbc-030" in {s["id"] for s in ctx["services"]}
    data = content()
    data["recommendations"][0]["goal_id"] = r.json()["life"]["goals"][0]["id"]
    enriched = enrich_analysis(AIAnalysis(customer_id=1, **data), ctx, {})
    assert enriched.recommendations[0].matched_services[0].id == "kbc-030"


def test_car_purchase_does_not_imply_insurance_and_no_keyword_guessing():
    from backend.services.service_matching import goal_services
    for title in ("buy a car", "do not offer car insurance", "review travel insurance"):
        assert not goal_services([dict(id=1,title=title,category="personal",status="active")], Preferences())
    assert "kbc-030" not in {s.id for s in match_services("mobility", Preferences(), confirmed=True)}
    assert not match_services("car_insurance", Preferences(), confirmed=False)
    assert not goal_services([dict(id=1,title="car insurance",category="personal",status="active")], Preferences(protected_categories=["personal"]))
