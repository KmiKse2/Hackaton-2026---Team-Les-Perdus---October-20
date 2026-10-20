from copy import deepcopy
from datetime import date, timedelta

import pytest

from backend.tests.test_api import client
from backend.tests.test_banking import payload, send, tx, consent
from backend.services.banking import today


def fact(value, **extra):
    return dict(value=value, source="declared", observed_at="2026-08-01", **extra)


def update(client, attributes=None, **extra):
    body = dict(extra)
    if attributes is not None:
        body["attributes"] = attributes
    result = client.post("/api/customers/1/context", json=body)
    assert result.status_code == 200, result.text
    return result.json()["customer_profile"]


def profile(client):
    return client.get("/api/customers/1/profile").json()


def signal(client, key):
    return profile(client)["signals"][key]


def complete_payload(client, rows):
    body = payload(client, rows)
    body["accounts"].append({"details": client.get("/api/accounts/demo-1-savings?customer_id=1").json(),
                             "historyFrom": "2026-07-01", "historyTo": "2026-09-30",
                             "transactions": {"booked": [], "pending": []}})
    return body


def test_catalog_has_40_signals_and_4_metadata_fields(client):
    catalog = client.get("/api/signal-catalog").json()
    assert len(catalog["signals"]) == 40
    assert len(catalog["metadata"]) == 4
    state = profile(client)
    assert len(state["signals"]) == 40
    for record in state["signals"].values():
        assert {"value", "source", "confidence", "timestamp", "status", "availability"}.issubset(record)
    assert state["synthetic"]


@pytest.mark.parametrize("dob,expected", [("1961-08-31", "51-65"), ("1960-08-31", "66+"), ("2000-09-01", "18-25"), ("2000-08-31", "26-35")])
def test_age_boundaries_and_birthdays(client, dob, expected):
    state = update(client, {"date_of_birth": {"value": dob, "source": "kyc", "observed_at": "2026-08-01"}})
    assert state["signals"]["age_group"]["value"] == expected


def test_declared_household_and_unknown_are_never_inferred(client):
    state = update(client, {"household_status": fact("married"), "dependents_count": fact(2)})
    assert state["signals"]["household_status"]["value"] == "married"
    state = update(client, {"household_status": None, "dependents_count": None, "date_of_birth": None})
    assert state["signals"]["household_status"]["value"] is None
    assert state["signals"]["age_group"]["availability"] == "unavailable"
    assert not any(c["key"] in {"age_group", "household_status"} for c in state["categories"])


def test_profile_validation_and_expiry(client):
    assert client.post("/api/customers/1/context", json={"attributes": {"dependents_count": fact(-1)}}).status_code == 422
    assert client.post("/api/customers/1/context", json={"attributes": {"dependents_count": fact(True)}}).status_code == 422
    assert client.post("/api/customers/1/context", json={"attributes": {"investment_risk_profile": fact("balanced")}}).status_code == 422
    assert client.post("/api/customers/1/context", json={"attributes": {"date_of_birth": fact("2030-01-01")}}).status_code == 422
    state = update(client, {"household_status": fact("married", valid_until="2026-08-02")})
    assert state["signals"]["household_status"]["status"] == "expired"
    assert state["signals"]["household_status"]["value"] is None


def test_personalization_consent_is_separate_from_bank_access(client):
    client.post("/api/customers/1/simulate")
    result = client.post("/api/customers/1/personalization-consent", json={"value": False, "source": "declared", "observed_at": str(today())})
    assert result.status_code == 200
    state = result.json()
    assert state["transactions"] and state["accounts"]  # Own account display still allowed.
    assert state["signals"] == state["events"] == state["customer_profile"]["categories"] == []
    assert state["kate_context"]["status"] == "blocked"
    assert all(s["value"] is None for key, s in state["customer_profile"]["signals"].items() if key != "personalization_consent")
    assert client.post("/api/customers/1/categories/student/feedback", json={"status": "confirmed"}).status_code == 403
    consent(client, consentStatus="revokedByPsu")
    result = client.post("/api/customers/1/personalization-consent", json={"value": True, "source": "declared", "observed_at": str(today())})
    assert result.json()["customer_profile"]["blocked"]  # No bypass of bank access.


def test_young_professional_classification_feedback_and_conflicting_fact(client):
    state = update(client, {"employment_status": fact("employed")})
    young = next(c for c in state["categories"] if c["key"] == "young_professional")
    assert young["status"] == "inferred"
    response = client.post("/api/customers/1/categories/young_professional/feedback", json={"status": "rejected"})
    assert response.status_code == 200
    assert next(c for c in profile(client)["categories"] if c["key"] == "young_professional")["status"] == "rejected"
    client.post("/api/customers/1/categories/young_professional/feedback", json={"status": "confirmed"})
    state = update(client, {"employment_status": fact("retired")})
    assert next(c for c in state["categories"] if c["key"] == "young_professional")["status"] == "expired"
    assert client.post("/api/customers/1/categories/household_status/feedback", json={"status": "confirmed"}).status_code == 422


def test_salary_patterns_and_income_changes(client):
    state = send(client, complete_payload(client, [tx("aug", when="2026-08-01"), tx("sept")]))["customer_profile"]
    s = state["signals"]
    assert s["new_recurring_salary"]["value"] is True
    assert s["income_change_percentage"]["value"][0]["percentage"] == "100.00"
    assert s["monthly_income"]["value"][0]["amount"] == "1666.67"
    assert any(c["key"] == "first_job" and c["status"] == "inferred" for c in state["categories"])
    assert s["employment_status"]["value"] == "student"  # Not silently overwritten.


def test_salary_missing_is_not_unemployment(client):
    send(client, complete_payload(client, [tx("jul", when="2026-07-01"), tx("aug", when="2026-08-01")]))
    assert signal(client, "salary_stopped")["value"]["detected"]
    assert signal(client, "employment_status")["value"] == "student"


def test_zero_income_baseline_has_no_infinite_percentage(client):
    send(client, complete_payload(client, [tx()]))
    change = signal(client, "income_change_percentage")["value"][0]
    assert change["previous_average"] == "0"
    assert change["percentage"] is None


def test_savings_flow_counts_one_leg_only(client):
    body = payload(client, [tx(), tx("save", amount="-500", code=None, creditorAccount={"iban": "DEMO-SAVINGS-1"})])
    saving = deepcopy(body["accounts"][0])
    saving["details"] = client.get("/api/accounts/demo-1-savings?customer_id=1").json()
    saving["transactions"]["booked"] = [tx("save-other-leg", amount="500", code=None, debtorAccount={"iban": "DEMO-CURRENT-1"})]
    body["accounts"].append(saving)
    state = send(client, body)["customer_profile"]
    rate = state["signals"]["monthly_savings_rate"]["value"][0]
    assert rate["net_savings_flow"] == "500"
    assert rate["rate_percent"] == "20.00"


def test_balance_history_trend_and_overdraft_frequency(client):
    body = payload(client, [])
    current = body["accounts"][0]
    current["balanceHistory"] = [{"balanceType": "closingBooked", "balanceAmount": {"amount": "-100" if day in (3, 4, 12, 13) else "1000", "currency": "EUR"}, "referenceDate": f"2026-09-{day:02}"} for day in range(1, 31)]
    savings = {"details": client.get("/api/accounts/demo-1-savings?customer_id=1").json(),
               "balanceHistory": [{"balanceType": "closingBooked", "balanceAmount": {"amount": str(amount), "currency": "EUR"}, "referenceDate": when} for when, amount in [("2026-07-31", 500), ("2026-08-31", 700), ("2026-09-30", 900)]]}
    body["accounts"].append(savings)
    state = send(client, body)["customer_profile"]
    assert state["signals"]["overdraft_frequency"]["value"][0]["episodes"] == 2
    assert state["signals"]["savings_trend"]["value"][0]["direction"] == "increasing"
    assert any(c["key"] == "wealth_growth" for c in state["categories"])


def test_products_are_unknown_until_a_complete_snapshot(client):
    assert signal(client, "loan_products")["value"] is None
    client.post("/api/customers/1/simulate")
    products = {"observed_at": "2026-09-30", "valid_until": "2027-01-01", "complete": True,
                "items": [{"id": "m1", "type": "mortgage", "status": "active", "started_at": "2026-09-05", "monthly_repayment": {"amount": "900", "currency": "EUR"}},
                          {"id": "i1", "type": "investments", "status": "active", "started_at": "2025-01-01", "valuation": {"amount": "10000", "currency": "EUR"}}]}
    state = update(client, products=products)
    assert state["signals"]["loan_products"]["value"] == ["mortgage"]
    assert state["signals"]["monthly_loan_commitment"]["value"][0]["amount"] == "900"
    assert state["signals"]["investment_balance"]["value"][0]["amount"] == "10000"
    assert state["signals"]["investment_risk_profile"]["value"] is None
    assert any(c["key"] == "home_purchase" for c in state["categories"])
    products["complete"] = False
    assert update(client, products=products)["signals"]["loan_products"]["value"] is None


def test_usage_logs_and_declared_channel_priority(client):
    usage = {"from_date": "2026-08-01", "to_date": "2026-08-31", "sessions": [
        {"id": "1", "date": "2026-08-02", "channel": "web"}, {"id": "2", "date": "2026-08-03", "channel": "web"},
        {"id": "3", "date": "2026-08-03", "channel": "mobile"}],
        "kate_interactions": [{"id": "k1", "date": "2026-08-03", "topic": "budget", "action": "ask", "outcome": "pending"}]}
    state = update(client, usage=usage)
    assert state["signals"]["preferred_channel"]["value"] == "mobile"
    assert state["signals"]["mobile_usage_frequency"]["value"]["sessions"] == 1
    assert len(state["signals"]["kate_interaction_history"]["value"]) == 1
    assert update(client, {"preferred_channel": None})["signals"]["preferred_channel"]["value"] == "web"


def test_student_grant_is_only_an_inferred_profile_without_declaration(client):
    update(client, {"employment_status": None})
    send(client, payload(client, [tx(code="STDY")]))
    student = next(c for c in profile(client)["categories"] if c["key"] == "student")
    assert student["status"] == "inferred"
    assert signal(client, "employment_status")["value"] is None


def test_unknown_inputs_do_not_create_forecasts_or_anomalies(client):
    state = profile(client)
    for key in ("low_balance_risk", "overdraft_frequency", "unusual_spending", "savings_trend", "loan_products"):
        assert state["signals"][key]["value"] is None


def test_partial_account_coverage_blocks_aggregate_income_change(client):
    send(client, payload(client, [tx("aug", when="2026-08-01"), tx("sept")]))
    assert signal(client, "income_change_percentage")["value"] is None


def test_forecast_and_financial_stress_have_supporting_evidence(client):
    rows = [tx("jul", when="2026-07-01"), tx("aug", when="2026-08-01")]
    body = complete_payload(client, rows)
    body["accounts"][0]["balances"] = [{"balanceType": "interimBooked", "balanceAmount": {"amount": "-100", "currency": "EUR"}, "referenceDate": "2026-09-30"}]
    state = send(client, body)["customer_profile"]
    assert state["signals"]["low_balance_risk"]["value"] is None  # Cannot invent the next overdue salary.
    assert any(c["key"] == "financial_stress" and c["status"] == "inferred" for c in state["categories"])


def test_forecast_before_next_income_uses_known_expenses(client):
    rows = [tx(f"s{month}", when=f"2026-{month:02}-15") for month in (7, 8, 9)]
    rows += [tx(f"r{month}", amount="-800", code="RENT", when=f"2026-{month:02}-05") for month in (7, 8, 9)]
    body = complete_payload(client, rows)
    body["accounts"][0]["balances"] = [{"balanceType": "interimBooked", "balanceAmount": {"amount": "500", "currency": "EUR"}, "referenceDate": "2026-09-30"}]
    state = send(client, body)["customer_profile"]
    forecast = state["signals"]["low_balance_risk"]["value"][0]
    assert forecast["projected_booked_balance"] == "-300.00"
    assert forecast["until"] == "2026-10-15"
    assert forecast["risk"]


def test_unusual_spending_and_foreign_activity(client):
    rows = [tx(f"old{i}", amount="-30", code=None, when=f"2026-07-{i:02}", creditorName="IKEA") for i in range(1, 7)]
    rows += [tx("large", amount="-700", code=None, creditorName="IKEA", merchantCountry="FR")]
    state = send(client, complete_payload(client, rows))["customer_profile"]
    assert len(state["signals"]["unusual_spending"]["value"]) == 1
    assert state["signals"]["foreign_transaction_activity"]["value"] is True


def test_historical_feedback_expires(client):
    from backend.database import SessionLocal
    from backend.models import CategoryFeedback
    from sqlalchemy import select
    update(client, {"employment_status": fact("employed")})
    client.post("/api/customers/1/categories/young_professional/feedback", json={"status": "confirmed"})
    with SessionLocal() as db:
        response = db.scalar(select(CategoryFeedback).where(CategoryFeedback.customer_id == 1))
        response.valid_until = today() - timedelta(days=1)
        db.commit()
    assert next(c for c in profile(client)["categories"] if c["key"] == "young_professional")["status"] == "expired"
