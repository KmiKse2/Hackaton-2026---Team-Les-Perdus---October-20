import pytest
from fastapi.testclient import TestClient

from backend.database import Base, engine
from backend.main import app


@pytest.fixture
def client():
    assert "lifeflow-pytest-" in str(engine.url.database), "Refusing to reset a non-test database"
    Base.metadata.drop_all(engine)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("customer_id,kind,score", [(1, "FIRST_JOB", 70), (2, "MOVING", 75), (3, "TRAVEL", 60)])
def test_scenario_and_reset(client, customer_id, kind, score):
    before = client.get(f"/api/customers/{customer_id}").json()
    assert before["events"] == []
    response = client.post(f"/api/customers/{customer_id}/simulate")
    assert response.status_code == 200
    after = response.json()
    assert after["events"][0]["type"] == kind
    assert after["events"][0]["score"] == score
    assert after["events"][0]["personalization"]["source"] == "local_preview_for_kate"
    # Replaying the simulation does not create transactions or events twice.
    assert client.post(f"/api/customers/{customer_id}/simulate").json() == after
    assert client.get(f"/api/customers/{customer_id}").json() == after
    reset = client.post(f"/api/customers/{customer_id}/reset").json()
    assert reset == before


def test_feedback_scoped_and_persistent(client):
    state = client.post("/api/customers/1/simulate").json()
    event_id = state["events"][0]["id"]
    assert client.post(f"/api/customers/2/insights/{event_id}/feedback", json={"status": "confirmed"}).status_code == 404
    result = client.post(f"/api/customers/1/insights/{event_id}/feedback", json={"status": "dismissed"})
    assert result.status_code == 200
    assert result.json()["events"][0]["status"] == "dismissed"
    assert client.post("/api/customers/1/simulate").json()["events"][0]["status"] == "dismissed"
    assert client.get("/api/customers/2").json()["events"] == []
    assert client.post(f"/api/customers/1/insights/{event_id}/feedback", json={"status": "invented"}).status_code == 422


def transaction(**overrides):
    return dict({"customer_id": 1, "date": "2026-09-01", "merchant": "Employeur fictif", "amount": "2000.50", "category": "salary", "reference": "user-test"}, **overrides)


def test_ingestion_validation_and_deduplication(client):
    result = client.post("/api/transactions", json=transaction())
    assert result.status_code == 201
    assert result.json()["events"] == []  # A single salary no longer implies a job change.
    assert client.post("/api/transactions", json=transaction()).status_code == 409
    assert client.post("/api/transactions", json=transaction(amount="1.001", reference="user-invalid")).status_code == 422
    assert client.post("/api/transactions", json=transaction(category="unknown")).status_code == 422
    assert client.post("/api/transactions", json=transaction(reference="seed-1")).status_code == 422
    assert client.post("/api/transactions", json=transaction(customer_id=999)).status_code == 404


def test_refund_is_not_a_salary_signal(client):
    result = client.post("/api/transactions", json=transaction(amount="-100.00"))
    assert result.status_code == 201
    assert result.json()["events"] == []


def test_event_expiry(client):
    client.post("/api/customers/1/simulate")
    result = client.post("/api/transactions", json=transaction(date="2027-01-01", category="groceries", amount="-20", reference="user-later"))
    assert result.json()["events"] == []


def test_unknown_customer_and_health(client):
    assert client.get("/api/customers/999").status_code == 404
    assert client.post("/api/customers/999/simulate").status_code == 404
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200


def test_optional_demo_token(client, monkeypatch):
    monkeypatch.setenv("DEMO_API_TOKEN", "test-token")
    assert client.get("/api/customers").status_code == 401
    assert client.post("/api/customers/1/reset").status_code == 401
    assert client.get("/api/customers", headers={"X-Demo-Token": "test-token"}).status_code == 200
