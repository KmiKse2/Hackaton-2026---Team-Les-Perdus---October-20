import json
from copy import deepcopy
from datetime import timedelta

import pytest

from backend.tests.test_api import client  # Shared isolated SQLite fixture.
from backend.services.banking import today


def consent(client, **changes):
    payload = client.get("/api/consents/demo-consent-1?customer_id=1").json()
    payload.update(changes)
    result = client.post("/api/customers/1/consent", json=payload)
    assert result.status_code == 200, result.text
    return result.json()


def tx(tid="s1", amount="2500.00", code="SALA", when="2026-09-01", **kwargs):
    return dict({"transactionId": tid, "transactionAmount": {"amount": amount, "currency": "EUR"},
                 "bookingDate": when, "purposeCode": code,
                 "debtorName": "Employeur privé confidentiel", "creditorName": "Bailleur confidentiel"}, **kwargs)


def payload(client, rows=None, pending=None, **changes):
    details = client.get("/api/accounts/demo-1-current?customer_id=1").json()
    item = {"details": details, "transactions": {"booked": rows or [], "pending": pending or []},
            "historyFrom": "2026-07-01", "historyTo": "2026-09-30"}
    item.update(changes)
    return {"consentId": "demo-consent-1", "asOf": "2026-09-30", "accounts": [item]}


def send(client, body, automatic=False):
    response = client.post(f"/api/customers/1/banking?automatic={str(automatic).lower()}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def kinds(state):
    return {s["type"] for s in state["signals"]}


def test_structured_salary_and_recurrence(client):
    one = send(client, payload(client, [tx()]))
    assert "new_salary" in kinds(one)
    assert "recurring_salary" not in kinds(one)
    assert one["events"] == []
    two = send(client, payload(client, [tx("july", when="2026-08-01"), tx("sept")]))
    assert "recurring_salary" in kinds(two)
    assert "new_salary" not in kinds(two)


def test_text_salary_and_short_history_do_not_imply_new_job(client):
    text = tx(code=None, remittanceInformationUnstructured="salary payment")
    one = send(client, payload(client, [text]))
    signal = next(s for s in one["signals"] if s["type"] == "salary_observed")
    assert signal["strength"] == "weak"
    assert "new_salary" not in kinds(one)
    short = send(client, payload(client, [tx()], historyFrom="2026-09-01"))
    assert "new_salary" not in kinds(short)
    assert short["data_quality"]["warnings"]


def test_internal_transfer_is_not_income_even_with_salary_code(client):
    row = tx(debtorAccount={"iban": "DEMO-SAVINGS-1"})
    state = send(client, payload(client, [row]))
    assert state["financial"]["income"] == "0"
    assert "new_salary" not in kinds(state)
    assert state["data_quality"]["internal_transfers"] == 1
    assert state["transactions"][0]["internal_transfer"]


def test_pending_promotion_idempotency_and_no_demotion(client):
    body = payload(client, pending=[tx()])
    pending = send(client, body)
    assert pending["financial"]["income"] == "0"
    assert pending["data_quality"]["pending_transactions"] == 1
    posted = send(client, payload(client, [tx()]))
    assert posted["financial"]["income"] == "2500.00"
    assert send(client, payload(client, [tx()])) == posted
    assert client.post("/api/customers/1/banking", json=body).status_code == 409
    report = client.get("/api/accounts/demo-1-current/transactions?customer_id=1").json()["transactions"]
    assert len(report["booked"]) == 1 and report["pending"] == []


@pytest.mark.parametrize("status", ["revokedByPsu", "expired", "terminatedByTpp"])
def test_revoked_consent_hides_cached_data_and_blocks_mutations(client, status):
    client.post("/api/customers/1/simulate")
    state = consent(client, consentStatus=status)
    assert state["signals"] == state["events"] == state["accounts"] == state["transactions"] == []
    assert state["financial"]["balance"] is None
    assert state["kate_context"]["status"] == "blocked"
    assert state["kate_context"]["observations"] == []
    assert client.post("/api/customers/1/simulate").status_code == 403
    assert client.post("/api/customers/1/reset").status_code == 403
    assert client.get("/api/accounts/demo-1-current/balances?customer_id=1").status_code == 403


def test_expiry_uses_real_date_not_replay_date(client):
    state = consent(client, validUntil=str(today() - timedelta(days=1)))
    assert state["consent"]["status"] == "expired"
    assert state["signals"] == []


def test_permissions_are_independent(client):
    client.post("/api/customers/1/simulate")
    state = consent(client, permissions=["accounts", "transactions"])
    assert state["signals"]
    assert state["financial"]["balance"] is None
    assert all(a["balances"] == [] for a in state["accounts"])
    assert client.get("/api/accounts/demo-1-current/balances?customer_id=1").status_code == 403
    state = consent(client, permissions=["accounts", "balances"])
    assert state["signals"] == state["transactions"] == []
    assert state["financial"]["income"] is None
    assert state["data_quality"]["coverage"] == []
    assert client.get("/api/accounts/demo-1-current/transactions?customer_id=1").status_code == 403


def test_scope_and_cross_customer_guards(client):
    body = payload(client, [tx()])
    consent(client, accountResourceIds=["demo-1-savings"])
    assert client.post("/api/customers/1/banking", json=body).status_code == 403
    assert client.get("/api/accounts/demo-1-current?customer_id=1").status_code == 403
    assert client.get("/api/accounts/demo-2-current?customer_id=1").status_code == 404
    assert client.get("/api/consents/demo-consent-2?customer_id=1").status_code == 404
    record = client.get("/api/consents/demo-consent-1?customer_id=1").json()
    record["accountResourceIds"] = ["demo-2-current"]
    assert client.post("/api/customers/1/consent", json=record).status_code == 403


def test_quota_does_not_charge_cached_reads(client):
    consent(client, frequencyPerDay=1)
    body = payload(client, [tx()])
    state = send(client, body, automatic=True)
    assert state["consent"]["automatic_syncs_remaining"] == 0
    for _ in range(3):
        assert client.get("/api/customers/1").status_code == 200
    assert client.post("/api/customers/1/banking?automatic=true", json=body).status_code == 429
    assert send(client, body)["signals"]  # Explicit manual sync is a separate mode.


def test_available_includes_credit_and_is_not_recomputed(client):
    balances = [{"balanceType": "interimAvailable", "balanceAmount": {"amount": "800", "currency": "EUR"},
                 "referenceDate": "2026-09-30", "creditLimitIncluded": True},
                {"balanceType": "interimBooked", "balanceAmount": {"amount": "-200", "currency": "EUR"},
                 "referenceDate": "2026-09-30"}]
    state = send(client, payload(client, [tx()], balances=balances))
    # Savings balance is stale after advancing to September and is excluded.
    assert state["financial"]["balance"] == "800"
    assert state["financial"]["booked_balance"] == "-200"
    assert state["financial"]["credit_limit_included"]
    assert "negative_booked_balance" in kinds(state)


def test_stale_balance_unavailable_not_zero(client):
    state = send(client, payload(client, [tx()]))
    assert state["financial"]["balance"] is None
    assert state["data_quality"]["stale_balances"] > 0


@pytest.mark.parametrize("changes", [{"usage": "ORG"}, {"status": "blocked"}, {"status": "deleted"}, {"usage": None}])
def test_non_personal_accounts_do_not_produce_life_signals(client, changes):
    body = payload(client, [tx()])
    body["accounts"][0]["details"].update(changes)
    state = send(client, body)
    assert "new_salary" not in kinds(state)
    assert state["data_quality"]["excluded_accounts"] == 1


def test_mandate_and_rent_increase(client):
    rows = [tx("r1", amount="-800", code="RENT", when="2026-08-01", mandateId="private-mandate"),
            tx("r2", amount="-1000", code="RENT", mandateId="private-mandate")]
    state = send(client, payload(client, rows))
    assert {"recurring_mandate", "rent_increase"}.issubset(kinds(state))
    assert not state["events"]  # Rent increase alone is not moving.


def test_kate_contract_has_no_raw_identity_and_keeps_refusal(client):
    state = client.post("/api/customers/1/simulate").json()
    eid = state["events"][0]["id"]
    client.post(f"/api/customers/1/insights/{eid}/feedback", json={"status": "dismissed"})
    context = client.get("/api/customers/1/kate-context").json()
    assert context["sent_to_kate"] is False
    assert context["hypotheses"] == []
    assert context["rejected_hypotheses"] == ["FIRST_JOB"]
    serialized = json.dumps(context)
    for forbidden in ("Thomas", "ACME", "DEMO-CURRENT", "ownerName", "debtorName", "remittanceInformation", "iban", "mandateId"):
        assert forbidden not in serialized


def test_currency_is_not_combined_and_mismatch_rejected(client):
    body = payload(client, [tx()])
    euro = deepcopy(body["accounts"][0])
    dollar = deepcopy(euro)
    dollar["details"].update(resourceId="usd-account", currency="USD", iban="DEMO-USD")
    dollar["transactions"]["booked"][0]["transactionAmount"]["currency"] = "USD"
    consent(client, accountResourceIds=["demo-1-current", "demo-1-savings", "usd-account"])
    body["accounts"].append(dollar)
    state = send(client, body)
    assert {f["currency"]: f["income"] for f in state["financial"]["by_currency"]} == {"EUR": "2500.00", "USD": "2500.00"}
    dollar["transactions"]["booked"][0]["transactionAmount"]["currency"] = "EUR"
    assert client.post("/api/customers/1/banking", json=body).status_code == 422


def test_raw_fields_roundtrip_and_report_validation(client):
    row = tx(endToEndId="demo-e2e", mandateId="demo-mandate", debtorAccount={"iban": "DEMO-EMPLOYER"}, debtorAgent="KREDBEBB",
             remittanceInformationStructured="+++123/4567/89012+++", bankTransactionCode={"domain": "PMNT", "family": "RCDT"})
    body = payload(client, [row])
    send(client, body)
    report = client.get("/api/accounts/demo-1-current/transactions?customer_id=1").json()["transactions"]
    assert report["booked"][0] == row
    body["accounts"][0]["transactions"]["pending"] = [row]
    assert client.post("/api/customers/1/banking", json=body).status_code == 422


def test_latest_booked_snapshot_and_no_double_count(client):
    balances = [{"balanceType": "interimBooked", "balanceAmount": {"amount": "-200", "currency": "EUR"}, "referenceDate": "2026-09-24"},
                {"balanceType": "closingBooked", "balanceAmount": {"amount": "100", "currency": "EUR"}, "referenceDate": "2026-09-30"}]
    state = send(client, payload(client, [tx()], balances=balances))
    assert state["financial"]["booked_balance"] == "100"
    assert "negative_booked_balance" not in kinds(state)


def test_failed_sync_is_atomic_and_does_not_consume_quota(client):
    consent(client, frequencyPerDay=1)
    posted = send(client, payload(client, [tx()]))
    invalid = payload(client, pending=[tx()])
    invalid["accounts"][0]["details"]["name"] = "Should not persist"
    assert client.post("/api/customers/1/banking?automatic=true", json=invalid).status_code == 409
    assert client.get("/api/customers/1").json() == posted


def test_bootstrap_preserves_legacy_rows_and_is_idempotent(client):
    from backend.bank_seed import bootstrap
    from backend.database import SessionLocal
    from backend.models import BankProfile, Customer, Transaction
    from sqlalchemy import select
    with SessionLocal() as db:
        rows_before = list(db.scalars(select(Transaction.id)))
        bootstrap(db)
        bootstrap(db)
        assert list(db.scalars(select(Transaction.id))) == rows_before
        assert len(list(db.scalars(select(BankProfile)))) == len(list(db.scalars(select(Customer))))
