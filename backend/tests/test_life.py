from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.tests.test_api import client
from backend.database import SessionLocal
from backend.models import ChatProposal, CustomerContext, LifeGoal, LifePlan
from backend.life_schemas import ConversationOutput, GoalInput, Preferences
from backend.services.life import calculate_plan


def preferences(client, **kw):
    return client.post("/api/customers/1/preferences", json=kw)


def goal(client, cid=1, **kw):
    return client.post(f"/api/customers/{cid}/goals", json=dict(title="My project", **kw))


def chat_response(**kw):
    return ConversationOutput.model_validate(dict(
        message="Let's clarify your goal. Review any proposed information before saving.",
        intent="goal", clarification_questions=["What budget do you have in mind?"],
        goal_updates=[], memory_updates=[], financing_preference=None,
        ecosystem_service_ids=[], next_best_action="clarify", **kw))


def mock_chat(monkeypatch, output):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    def generate(context, schema, model, instructions):
        assert context["user_message"]
        assert "untrusted" in instructions
        return output
    monkeypatch.setattr("backend.services.ai_analysis.generate_structured", generate)


def test_plan_example_and_priority_no_double_count():
    prefs = Preferences(budgets=[{"currency": "EUR", "savings_pool": "9000", "emergency_reserve": "3000", "monthly_capacity": "300"}])
    g = GoalInput(title="Car", target_amount="30000", target_date="2031-10-20", allocated_savings="6000", monthly_contribution="300").model_dump(mode="json")
    other = GoalInput(title="Trip", target_amount="6000", target_date="2031-10-20").model_dump(mode="json")
    plan = calculate_plan([dict(g, id=1), dict(other, id=2)], prefs, date(2026, 10, 20))
    p = plan["goals"][0]
    assert p["remaining"] == "24000" and p["months_available"] == 60
    assert p["monthly_required"] == "400.00" and p["monthly_gap"] == "100.00"
    assert p["scenarios"][0]["months"] == 80
    assert plan["budgets"][0]["unallocated"] == "0"
    assert plan["goals"][1]["scenarios"][-1]["monthly"] == "0"


def test_goals_crud_reserve_revision_and_isolation(client):
    assert preferences(client, budgets=[{"currency": "EUR", "savings_pool": "9000", "emergency_reserve": "3000", "monthly_capacity": "300"}]).status_code == 200
    r = goal(client, target_amount="30000", allocated_savings="6000", monthly_contribution="300", target_date="2031-10-20")
    assert r.status_code == 200, r.text
    g = r.json()["life"]["goals"][0]
    assert goal(client, allocated_savings="1").status_code == 422
    assert goal(client, cid=2, allocated_savings="1").status_code == 422
    assert client.post(f"/api/customers/2/goals/{g['id']}/delete").status_code == 404
    assert client.post(f"/api/customers/1/goals/{g['id']}", json={"title": "Changed", "expected_revision": 99}).status_code == 409
    assert preferences(client, budgets=[{"currency": "EUR", "savings_pool": "6000", "emergency_reserve": "3000"}]).status_code == 422
    with SessionLocal() as db:
        assert db.get(LifePlan, 1).data["goals"]
    assert client.post(f"/api/customers/1/goals/{g['id']}/delete").json()["life"]["goals"] == []


def test_arbitrary_nonfinancial_goals_and_currencies(client):
    assert goal(client, category="cycling_race", amount_basis="non_financial").status_code == 200
    assert goal(client, amount_basis="non_financial", allocated_savings="5").status_code == 422
    assert goal(client, target_amount="-1").status_code == 422
    assert goal(client, currency="USD", allocated_savings="1").status_code == 422
    assert goal(client, category="retirement", target_amount="10000").status_code == 200


def test_chat_proposals_require_confirmation_and_corrections(client, monkeypatch):
    output = chat_response()
    output.goal_updates = ConversationOutput.model_validate({
        **output.model_dump(), "goal_updates": [{"goal_id": None, "title": "Buy a home", "category": "housing",
        "description": "My own home", "motivation": "", "target_amount": None, "target_date": "2031-10-20",
        "currency": "EUR", "amount_basis": "property_deposit"}]}).goal_updates
    output.financing_preference = "saving"
    mock_chat(monkeypatch, output)
    r = client.post("/api/customers/1/chat", json={"message": "I want to buy a house in five years. I prefer saving."})
    assert r.status_code == 200, r.text
    assert r.json()["life"]["goals"] == []
    proposal = r.json()["life"]["pending_proposals"][0]
    assert client.post(f"/api/customers/2/proposals/{proposal['id']}/confirm", json={"accept": True}).status_code == 404
    r = client.post(f"/api/customers/1/proposals/{proposal['id']}/confirm", json={"accept": True})
    assert r.status_code == 200, r.text
    assert r.json()["life"]["goals"][0]["title"] == "Buy a home"
    assert r.json()["life"]["preferences"]["financing_preference"] == "saving"
    assert client.post(f"/api/customers/1/proposals/{proposal['id']}/confirm", json={"accept": True}).status_code == 409
    assert preferences(client, financing_preference="compare").status_code == 200
    from backend.services.prompt_builder import build_context
    with SessionLocal() as db:
        ctx = build_context(db, 1)
        assert ctx["preferences"]["financing_preference"] == "compare"
        assert ctx["goals"][0]["title"] == "Buy a home"
        assert any(s["id"] == "kbc_myhome" for s in ctx["services"])


def test_memory_hobby_review_delete_stale_proposal(client, monkeypatch):
    output = chat_response()
    from backend.life_schemas import MemoryChange
    output.memory_updates = [MemoryChange(key="hobbies", value="Cycling")]
    mock_chat(monkeypatch, output)
    r = client.post("/api/customers/1/chat", json={"message": "I love cycling."}).json()
    pid = r["life"]["pending_proposals"][0]["id"]
    assert "hobbies" not in r["life"]["information"]
    assert client.post(f"/api/customers/1/proposals/{pid}/confirm", json={"accept": True}).status_code == 200
    r = client.post("/api/customers/1/chat", json={"message": "I prefer running now."}).json()
    pid = r["life"]["pending_proposals"][0]["id"]
    client.post("/api/customers/1/context", json={"attributes": {"hobbies": {"value": "Running", "source": "declared", "observed_at": "2026-10-20"}}})
    # A direct correction removes pending proposals and raw history to prevent resurrection.
    assert client.post(f"/api/customers/1/proposals/{pid}/confirm", json={"accept": True}).status_code == 404
    assert client.post("/api/customers/1/memory/delete").json()["life"]["information"] == {}
    assert client.get("/api/customers/1").json()["chat"]["messages"] == []


def test_chat_invalid_services_failure_and_consent(client, monkeypatch):
    output = chat_response()
    output.ecosystem_service_ids = ["invented"]
    mock_chat(monkeypatch, output)
    assert client.post("/api/customers/1/chat", json={"message": "Help"}).status_code == 502
    client.post("/api/customers/1/personalization-consent", json={"value": False, "source": "declared", "observed_at": "2026-10-20"})
    assert goal(client).status_code == 403
    assert client.post("/api/customers/1/chat", json={"message": "Help"}).status_code == 403
    assert client.get("/api/customers/1").json()["life"]["subscriptions"] == []


def test_memory_disabled_does_not_persist_chat(client, monkeypatch):
    assert preferences(client, memory_enabled=False).status_code == 200
    mock_chat(monkeypatch, chat_response())
    r = client.post("/api/customers/1/chat", json={"message": "Help me think."})
    assert r.status_code == 200, r.text
    assert r.json()["chat"]["last_response"]["ephemeral"]
    assert client.get("/api/customers/1").json()["chat"]["messages"] == []


def test_trigger_control_and_catalogue(client):
    from backend.services.ecosystem import catalogue
    assert all(s["integration_status"] == "not_connected" for s in catalogue())
    assert goal(client, target_amount="1000", target_date="2020-01-01").status_code == 200
    view = client.get("/api/customers/1/life").json()
    alert = view["notifications"][0]
    assert client.post(f"/api/customers/1/recommendations/{alert['id']}/feedback", json={"status": "postponed"}).status_code == 200
    assert not client.get("/api/customers/1/life").json()["notifications"]
    assert preferences(client, notifications=False).status_code == 200
    assert not client.get("/api/customers/1/life").json()["notifications"]


def test_subscription_annualisation_usefulness_scope_and_opt_in(client):
    for month in (6, 7, 8):
        r = client.post("/api/transactions", json={
            "customer_id":1, "date":f"2026-{month:02}-15", "merchant":"Example Gym",
            "amount":"-39", "category":"subscription", "reference":f"user-gym-{month}"})
        assert r.status_code == 201, r.text
    view = client.get("/api/customers/1/life").json()
    sub = next(s for s in view["subscriptions"] if s["merchant"] == "Example Gym")
    assert sub["estimated_annual_cost"] is None and sub["usage_status"] == "unknown"
    assert sub["potential_annual_saving"] is None
    body = {"confirmed": True, "frequency":"monthly", "usage_status":"regularly", "importance":"valued", "customer_decision":"keep"}
    assert client.post(f"/api/customers/2/subscriptions/{sub['id']}", json=body).status_code == 404
    r = client.post(f"/api/customers/1/subscriptions/{sub['id']}", json=body)
    updated = next(s for s in r.json()["life"]["subscriptions"] if s["id"] == sub["id"])
    assert Decimal(updated["estimated_annual_cost"]) == Decimal("468")
    assert updated["potential_annual_saving"] is None
    body.update(usage_status="unused", customer_decision="consider_cancelling")
    r = client.post(f"/api/customers/1/subscriptions/{sub['id']}", json=body)
    updated = next(s for s in r.json()["life"]["subscriptions"] if s["id"] == sub["id"])
    assert Decimal(updated["potential_annual_saving"]) == Decimal("468")
    assert "No cancellation" in updated["note"]
    body.update(usage_status="do_not_ask")
    client.post(f"/api/customers/1/subscriptions/{sub['id']}", json=body)
    assert not any(sub["id"][:24] in n["id"] for n in client.get("/api/customers/1/life").json()["notifications"])


def test_large_recurring_price_change_and_annual_candidate(client):
    for i, (day, merchant, amount) in enumerate([
        ("2026-06-15", "Price review", "-39"), ("2026-07-15", "Price review", "-39"),
        ("2026-08-15", "Price review", "-59"),
        ("2025-08-01", "Annual review", "-120"), ("2026-08-01", "Annual review", "-120"),
    ]):
        assert client.post("/api/transactions", json={"customer_id":1,"date":day,"merchant":merchant,
            "amount":amount,"category":"subscription","reference":f"user-pattern-{i}"}).status_code == 201
    subs = client.get("/api/customers/1/life").json()["subscriptions"]
    change = next(s for s in subs if s["merchant"] == "Price review")
    assert Decimal(change["increase_percent"]) > 50
    yearly = next(s for s in subs if s["merchant"] == "Annual review")
    assert yearly["detected_frequency"] == "annual"
    assert Decimal(yearly["projected_annual_cost"]) == 120
    assert yearly["estimated_annual_cost"] is None


def test_stress_scenarios_do_not_change_goals_or_double_spend(client):
    preferences(client, budgets=[{"currency":"EUR","savings_pool":"9000","emergency_reserve":"3000","monthly_capacity":"300"}])
    saved = goal(client, target_amount="30000", allocated_savings="6000", monthly_contribution="300", target_date="2031-10-20").json()["life"]["goals"]
    r = client.post("/api/customers/1/plans/scenario", json={"monthly_capacity_reduction":"100","unexpected_expense":"2000","annual_cost_growth_percent":"2"})
    assert r.status_code == 200, r.text
    assert r.json()["life"]["goals"] == saved
    scenario = r.json()["life"]["plan"]["stress_scenario"]
    assert scenario["budgets"][0]["monthly_capacity"] == "200"
    assert scenario["warnings"]
    assert client.get("/api/customers/1/life").json()["plan"]["stress_scenario"] == scenario


def test_insurance_overlap_is_not_claimed_as_duplicate(client):
    policies = [
        {"label":"Card cover","scope":"family travel","risks":["medical"],"start_date":"2026-01-01","end_date":"2027-12-31"},
        {"label":"Travel policy","scope":"family travel","risks":["medical","baggage"],"start_date":"2026-08-01","end_date":"2027-08-01"},
    ]
    r = preferences(client, insurance_policies=policies)
    assert r.status_code == 200, r.text
    checks = r.json()["life"]["coverage_checks"]
    assert checks[0]["shared_risks"] == ["medical"]
    assert checks[0]["status"] == "possible_overlap_requires_policy_review"


def test_erasure_after_withdrawal_and_injection_list_rejection(client):
    goal(client)
    client.post("/api/customers/1/personalization-consent", json={"value":False,"source":"declared","observed_at":"2026-10-20"})
    r = client.post("/api/customers/1/life/delete")
    assert r.status_code == 200
    assert r.json()["life"]["goals"] == []
    assert r.json()["life"]["information"] == {}
    assert r.json()["life"]["blocked"]
    from pydantic import ValidationError
    bad = chat_response().model_dump()
    bad["clarification_questions"] = ["<script>execute()</script>"]
    with pytest.raises(ValidationError):
        ConversationOutput.model_validate(bad)


def test_workbook_import_is_catalogue_only(tmp_path):
    from openpyxl import Workbook
    from backend.services.ecosystem import import_catalogue, catalogue
    book = Workbook()
    sheet = book.active
    sheet.append(["id","name","provider","ecosystem","description","supported_goal_categories","required_information"])
    sheet.append(["example","Imported example","Example provider","Other","Unverified example",'["personal"]','[]'])
    source, destination = tmp_path / "catalogue.xlsx", tmp_path / "catalogue.json"
    book.save(source)
    import_catalogue(source, destination)
    record = catalogue(destination)[0]
    assert record["verification_status"] == "catalogue_only"
    assert record["verified_url"] is None
    assert record["integration_status"] == "not_connected"


@pytest.mark.parametrize("kind", ["retirement", "hobby"])
def test_goal_specific_chat_context_and_provider_failure(client, monkeypatch, kind):
    from fastapi import HTTPException
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    def unavailable(*args):
        raise HTTPException(503, "AI analysis is temporarily unavailable")
    monkeypatch.setattr("backend.services.ai_analysis.generate_structured", unavailable)
    assert client.post("/api/customers/1/chat", json={"message":kind}).status_code == 503
    assert client.get("/api/customers/1").json()["chat"]["messages"] == []
    output = chat_response()
    output.clarification_questions = ["When would you like to retire?"] if kind == "retirement" else ["Is cycling connected to a particular goal?"]
    mock_chat(monkeypatch, output)
    r = client.post("/api/customers/1/chat", json={"message": "I want to prepare for retirement." if kind == "retirement" else "I love cycling."})
    assert r.status_code == 200
    assert not r.json()["life"]["goals"]
    assert output.clarification_questions[0] in r.json()["chat"]["messages"][-1]["text"]
