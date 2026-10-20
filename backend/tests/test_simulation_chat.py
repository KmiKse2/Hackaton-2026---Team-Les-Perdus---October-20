from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.tests.test_api import client
from backend.database import SessionLocal
from backend.models import BalanceObservation, BankTransaction
from backend.services.solutions import proposals


def advance(client, cid=1, months=1):
    state = client.get(f"/api/customers/{cid}").json()
    return client.post(f"/api/customers/{cid}/simulate", json={"months": months, "expected_as_of": state["analysis_date"]})


def test_monthly_history_balances_and_retries(client):
    before = client.get('/api/customers/1').json()
    response = advance(client, months=3)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['analysis_date'] == '2026-11-30'
    assert [m['month'] for m in state['simulation_timeline']] == ['2026-09', '2026-10', '2026-11']
    assert state['customer_profile']['signals']['monthly_income']['availability'] == 'available'
    with SessionLocal() as db:
        txs = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == 'demo-1-current')))
        added = [t for t in txs if t.transaction_id.startswith('monthly-')]
        expected = Decimal(before['financial']['booked_balance']) + sum(Decimal(t.payload['transactionAmount']['amount']) for t in added)
        assert Decimal(state['financial']['booked_balance']) == expected
        dates = {b.reference_date for b in db.scalars(select(BalanceObservation).where(BalanceObservation.account_id == 'demo-1-current'))}
        assert len(dates) >= 91
    retry = client.post('/api/customers/1/simulate', json={'months':3, 'expected_as_of':before['analysis_date']})
    assert retry.status_code == 409
    assert client.get('/api/customers/1').json() == state
    assert advance(client, months=1).json()['analysis_date'] == '2026-12-31'


def test_year_rollover_and_reset(client):
    state = advance(client, cid=3, months=6).json()
    assert state['analysis_date'] == '2027-02-28'
    assert len(state['simulation_timeline']) == 6
    reset = client.post('/api/customers/3/reset').json()
    assert reset['analysis_date'] == '2026-08-31'
    assert reset['simulation_timeline'] == []
    with SessionLocal() as db:
        assert not [b for b in db.scalars(select(BalanceObservation).where(BalanceObservation.account_id == 'demo-3-current')) if str(b.reference_date) > '2026-08-31']
    assert advance(client, cid=3).status_code == 200


@pytest.mark.parametrize('months', [0,13,1.5,True])
def test_invalid_duration(client, months):
    assert advance(client, months=months).status_code == 422


def test_travel_proposals_chat_and_dismissal(client):
    state = advance(client, cid=3).json()
    options = {p['id']: p for p in state['chat']['proposals']}
    assert set(options) == {'travel_insurance', 'exchange_rates', 'foreign_cash'}
    assert all(p['requires_confirmation'] for p in options.values())
    assert state['kate_context']['solution_proposals'] == state['chat']['proposals']
    assert state['kate_context']['sent_to_kate'] is False
    response = client.post('/api/customers/3/chat', json={'proposal_id':'exchange_rates'}).json()
    assert len(response['chat']['messages']) == 2
    assert 'No live rates' in response['chat']['messages'][-1]['text']
    assert client.get('/api/customers/3').json()['chat'] == response['chat']
    assert client.get('/api/customers/1').json()['chat']['messages'] == []
    assert client.post('/api/customers/1/chat', json={'proposal_id':'exchange_rates'}).status_code == 409
    event_id = state['events'][0]['id']
    dismissed = client.post(f'/api/customers/3/insights/{event_id}/feedback', json={'status':'dismissed'}).json()
    assert dismissed['chat']['proposals'] == []
    assert dismissed['chat']['messages'] == []
    assert client.post('/api/customers/3/chat', json={'proposal_id':'exchange_rates'}).status_code == 409


def test_confirmation_updates_proposal_and_consent_blocks_chat(client):
    state = advance(client, cid=2).json()
    assert {p['id'] for p in state['chat']['proposals']} == {'home_insurance','moving_budget'}
    event_id = state['events'][0]['id']
    confirmed = client.post(f'/api/customers/2/insights/{event_id}/feedback', json={'status':'confirmed'}).json()
    assert all(not p['requires_confirmation'] for p in confirmed['chat']['proposals'])
    consent = {'value':False, 'source':'declared', 'observed_at':'2026-08-01'}
    blocked = client.post('/api/customers/2/personalization-consent', json=consent).json()
    assert blocked['chat']['blocked'] and not blocked['chat']['proposals']
    assert blocked['simulation_timeline'][0]['events'] == []
    assert client.post('/api/customers/2/chat', json={'message':'hello'}).status_code == 403


def test_chat_validation_and_untrusted_text(client):
    assert client.post('/api/customers/1/chat', json={'message':'  '}).status_code == 422
    assert client.post('/api/customers/1/chat', json={'message':'x'*1001}).status_code == 422
    text = '<script>alert(1)</script> place an order'
    state = client.post('/api/customers/1/chat', json={'message':text}).json()
    assert state['chat']['messages'][0]['text'] == text
    assert 'nothing has been purchased or sent' in state['chat']['messages'][1]['text']


def test_financial_pressure_prioritises_support():
    profile = {'blocked':False, 'categories':[{'key':'financial_stress','status':'inferred'}, {'key':'travelling','status':'inferred'}]}
    assert [p['id'] for p in proposals([], profile)] == ['budget_support']


def test_twelve_months_and_batch_rollback(client, monkeypatch):
    import backend.main as main
    original = main.append_month
    calls = 0
    def failing(db, customer):
        nonlocal calls
        calls += 1
        if calls == 2:
            from fastapi import HTTPException
            raise HTTPException(409, 'Simulated failure')
        return original(db, customer)
    before = client.get('/api/customers/1').json()
    monkeypatch.setattr(main, 'append_month', failing)
    assert advance(client, months=3).status_code == 409
    assert client.get('/api/customers/1').json() == before
    monkeypatch.setattr(main, 'append_month', original)
    result = advance(client, months=12)
    assert result.status_code == 200, result.text
    assert result.json()['analysis_date'] == '2027-08-31'
    assert len(result.json()['simulation_timeline']) == 12


def test_reduced_scope_hides_timeline(client):
    advance(client, months=1)
    consent = client.get('/api/consents/demo-consent-1?customer_id=1').json()
    consent['accountResourceIds'] = ['demo-1-savings']
    result = client.post('/api/customers/1/consent',json=consent).json()
    assert result['simulation_timeline'] == []
