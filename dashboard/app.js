const $ = (id) => document.getElementById(id);
const money = (value, currency = 'EUR') => value == null ? 'Unavailable' : new Intl.NumberFormat('en-GB', { style: 'currency', currency }).format(Number(value));
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let selected = 1;
let customers = [];
let state;
let busy = false;
let refreshing = false;
let revision = 0;
let token = sessionStorage.getItem('lifeflow-token') || '';
const symbols = { FIRST_JOB: '↗', MOVING: '⌂', TRAVEL: '✈' };

async function api(path, method = 'GET', body) {
  const response = await fetch(`/api${path}`, { method, headers: { 'Content-Type': 'application/json', 'X-Demo-Token': token }, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(30000) });
  if (!response.ok) {
    if (response.status === 401) $('token-form').hidden = false;
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Unable to reach LifeFlow. Check the server and try again.');
  }
  const result = await response.json();
  if (result.customer && (!result.chat || !result.simulation_timeline)) {
    throw new Error('The API is running an older version. Restart the LifeFlow server and refresh this page.');
  }
  return result;
}

function buttons() {
  const canSimulate = state && ['accounts', 'balances', 'transactions'].every((p) => state.consent.effective_permissions.includes(p));
  $('simulate').disabled = busy || !canSimulate;
  $('simulate').textContent = busy ? 'Processing…' : '▶ Simulate next months';
  $('simulation-months').disabled = busy || !canSimulate;
  $('chat-send').disabled = busy || !state?.chat || state.chat.blocked;
  $('chat-input').disabled = !state?.chat || state.chat.blocked;
  $('reset').disabled = busy || !canSimulate;
  $('consent-toggle').disabled = busy || !state;
  $('personalization-toggle').disabled = busy || !state;
  $('profile-save').disabled = busy || !state;
  document.querySelectorAll('.customer-tab, .confirmation button, .action-button, [data-proposal]').forEach((button) => { button.disabled = busy; });
}

async function perform(task) {
  if (busy) return;
  busy = true;
  revision += 1;
  $('error').hidden = true;
  document.body.classList.add('busy');
  try { buttons(); await task(); } catch (error) { $('error').textContent = error.message; $('error').hidden = false; }
  finally { busy = false; document.body.classList.remove('busy'); buttons(); }
}

function render() {
  const { customer, financial, signals, events, transactions } = state;
  $('customers').innerHTML = customers.map((c) => `<button class="customer-tab" data-customer="${c.id}" aria-pressed="${c.id === customer.id}"><span class="tab-initials">${escapeHtml(c.initials)}</span>${escapeHtml(c.name)}</button>`).join('');
  $('phone-avatar').textContent = customer.initials;
  $('greeting').textContent = `Hello ${customer.name} ☀`;
  $('balance').textContent = money(financial.balance, financial.currency);
  $('balance-note').textContent = `Bank snapshot · ${financial.currency} · ${state.analysis_date}`;
  $('credit-note').hidden = !financial.credit_limit_included && !financial.credit_limit_unknown;
  $('credit-note').textContent = financial.credit_limit_included ? 'The available balance includes a credit limit, so it is not all your own money.' : 'Whether a credit limit is included is unknown.';
  $('income').textContent = money(financial.income, financial.currency);
  $('expenses').textContent = money(financial.expenses, financial.currency);
  $('month').textContent = financial.period;
  $('period').textContent = financial.period;
  $('signal-count').textContent = signals.length;
  $('engine-status').textContent = `Analysis as of ${state.analysis_date}`;
  $('signals').innerHTML = signals.length ? signals.map((s) => `<details class="signal-detail"><summary><span class="signal-check">${s.strength === 'strong' ? '✓' : '~'}</span><span>${escapeHtml(s.label)}</span><small>${s.strength === 'strong' ? 'STRUCTURED' : 'TEXTUAL CLUE'}</small></summary><div class="signal-proof"><p>Account: ${escapeHtml(s.account_id)}</p><p>Fields: ${s.source_fields.map(escapeHtml).join(', ')}</p><p>Transactions: ${s.transaction_ids.length ? s.transaction_ids.map(escapeHtml).join(', ') : 'Account or balance context'}</p><p>${Object.entries(s.metrics).map(([key, value]) => `${escapeHtml(key)} : ${escapeHtml(value)}`).join(' · ')}</p>${s.limitations.map((l) => `<p>Limitation: ${escapeHtml(l)}</p>`).join('')}</div></details>`).join('') : `<div class="empty-state"><strong>${state.kate_context.status === 'blocked' ? 'Analysis paused.' : 'No usable signals.'}</strong>Permissions and data quality determine what can be observed.</div>`;
  renderBanking();
  renderProfile();
  renderChat();
  $('simulation-timeline').innerHTML = state.simulation_timeline.length ? state.simulation_timeline.map((m) => `<div class="month-summary"><strong>${escapeHtml(m.month)}</strong><span>${money(m.financial.booked_balance, m.financial.currency)}</span><small>${m.events.map(escapeHtml).join(' · ') || 'No new life event'}</small></div>`).join('') : '<p>No monthly simulation yet. Choose a duration to start.</p>';
  const statuses = { pending: 'Awaiting customer confirmation', confirmed: 'Confirmed by the customer', dismissed: 'Dismissed by the customer' };
  $('events').innerHTML = events.length ? events.map((e) => `<div class="event"><div class="event-title"><span>${symbols[e.type] || '◇'} ${escapeHtml(e.personalization.label)}</span><b>${e.score}/100</b></div><div class="progress"><span style="width:${e.score}%"></span></div><div class="event-meta"><span>${statuses[e.status]}</span><span>${e.evidence.length} clues</span></div><details><summary>Why this hypothesis?</summary><ul>${e.evidence.map((v) => `<li>${escapeHtml(v.label)} · +${v.weight} points</li>`).join('')}</ul></details></div>`).join('') : '<div class="empty-state"><strong>No change detected.</strong>Suggestions appear only when there is enough evidence.</div>';
  const active = events.filter((e) => e.status !== 'dismissed');
  $('personalization').innerHTML = active.length ? active.map((e) => `<div class="insight-card ${e.status}"><div class="insight-symbol">${symbols[e.type] || '◇'}</div>${e.status === 'confirmed' ? '<div class="confirmed-tag">✓ CONFIRMED BY YOU</div>' : ''}<h3>${escapeHtml(e.personalization.title)}</h3><p>${escapeHtml(e.status === 'confirmed' ? 'Thank you for confirming. Here is an option for your next step.' : e.personalization.message)}</p>${e.status === 'pending' ? `<div class="confirmation"><button class="primary" data-feedback="confirmed" data-id="${e.id}">Yes, that is right</button><button class="secondary" data-feedback="dismissed" data-id="${e.id}">No</button></div>` : `<button class="action-button" data-plan="${e.id}">${escapeHtml(e.personalization.action)} →</button>`}</div>`).join('') : `<div class="welcome-card"><div class="insight-symbol">✳</div><h3>${events.length ? 'Thank you for your feedback.' : 'Here for you at the right time.'}</h3><p>${events.length ? 'This suggestion has been removed. You stay in control.' : 'As your life changes, your support can change with it.'}</p></div>`;
  $('transactions').innerHTML = transactions.slice(0, 4).map((t) => `<div class="tx"><div class="tx-icon">${Number(t.amount) > 0 ? '↙' : '↗'}</div><div class="tx-description"><strong>${escapeHtml(t.merchant)}</strong><small>${escapeHtml(t.date)}${t.internal_transfer ? ' · Between your accounts' : ' · Booked'}</small></div><span class="tx-amount ${Number(t.amount) > 0 ? 'positive' : ''}">${Number(t.amount) > 0 ? '+' : ''}${money(t.amount, t.currency)}</span></div>`).join('');
  buttons();
}

function renderChat() {
  const chat = state.chat;
  $('chat-intro').textContent = chat.blocked ? 'Personalisation is paused. Enable the required consent to see proposals.' : chat.intro;
  $('chat-proposals').innerHTML = chat.proposals.map((p) => `<button type="button" class="proposal" data-proposal="${escapeHtml(p.id)}"><strong>${escapeHtml(p.title)}</strong><span>${p.requires_confirmation ? 'If this situation applies to you: ' : ''}${escapeHtml(p.description)}</span></button>`).join('');
  $('chat-messages').innerHTML = chat.messages.map((m) => `<div class="chat-message ${m.role === 'user' ? 'user' : 'assistant'}"><small>${m.role === 'user' ? 'You' : 'Kate · demo'}</small><p>${escapeHtml(m.text)}</p></div>`).join('');
}

function renderBanking() {
  const { consent, accounts, data_quality: quality, kate_context: kate } = state;
  const valid = consent.status === 'valid';
  $('consent-status').textContent = valid ? 'VALID' : consent.status.toUpperCase();
  $('consent-summary').textContent = `${consent.effective_permissions.join(' · ') || 'No active permissions'} · Valid until ${consent.valid_until || '—'} · ${consent.automatic_syncs_remaining} automatic sync(s) remaining today`;
  $('consent-toggle').textContent = valid ? 'Revoke demo consent' : 'Restore demo consent';
  const types = { interimAvailable: 'Available', interimBooked: 'Booked', closingBooked: 'Closing booked', expected: 'Expected' };
  $('accounts').innerHTML = accounts.map((a) => `<details class="account-item"><summary>${escapeHtml(a.name || a.resourceId)} <small>${escapeHtml(a.cashAccountType || '?')} · ${escapeHtml(a.currency)} · ${escapeHtml(a.usage || '?')} · ${escapeHtml(a.status)}</small></summary>${a.balances.map((b) => `<p>${types[b.balanceType]} : <strong>${money(b.balanceAmount.amount, b.balanceAmount.currency)}</strong> · ${escapeHtml(b.referenceDate)}${b.creditLimitIncluded ? ' · credit included' : ''}</p>`).join('') || '<p>Balance unavailable or not supplied.</p>'}</details>`).join('');
  $('quality').innerHTML = `<p>${quality.pending_transactions} pending excluded · ${quality.internal_transfers} internal transfer(s) · ${quality.excluded_accounts} account(s) excluded from personal analysis · ${quality.stale_balances} stale balance(s)</p>${quality.warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join('')}<p>Income excludes transfers between own accounts. Currencies are not added together.</p>`;
  $('kate-summary').textContent = kate.status === 'blocked' ? 'Context blocked: required consent or data access is missing.' : `${kate.observations.length} observation(s) · ${kate.hypotheses.length} hypothesis(es) · ${kate.rejected_hypotheses.length} remembered dismissal(s)`;
  $('kate-context').textContent = JSON.stringify(kate, null, 2);
}

const profileLabels = { student: 'Student', employed: 'Employed', self_employed: 'Self-employed', unemployed: 'Unemployed', retired: 'Retired', single: 'Single', married: 'Married', cohabiting: 'Cohabiting', divorced: 'Divorced', under_18: 'Under 18', '66+': '66 and over', unknown: 'Unknown' };
const statusLabels = { inferred: 'Hypothesis', confirmed: 'Confirmed / declared', rejected: 'Dismissed', expired: 'Expired', observed: 'Observed', unknown: 'Unknown' };
function renderProfile() {
  const profile = state.customer_profile;
  const signals = profile.signals;
  $('profile-status').textContent = profile.blocked ? 'PAUSED' : profile.synthetic ? 'DEMO PROFILE' : 'PROVIDED PROFILE';
  $('profile-categories').innerHTML = profile.categories.length ? profile.categories.map((c) => `<div class="profile-category ${c.status}"><strong>${escapeHtml(c.value === true ? c.label : `${c.label} : ${profileLabels[c.value] || c.value}`)}</strong><small>${statusLabels[c.status]} · ${escapeHtml(c.source)} · ${escapeHtml(c.timestamp)}${c.confidence != null && c.status === 'inferred' ? ` · score ${Math.round(c.confidence * 100)}/100` : ''}</small>${c.source === 'inferred' ? `<div class="category-feedback"><button class="secondary" data-category="${escapeHtml(c.key)}" data-response="confirmed">Confirm</button><button class="secondary" data-category="${escapeHtml(c.key)}" data-response="rejected">Dismiss</button></div>` : ''}</div>`).join('') : '<p>No available categories. Check sources and consent.</p>';
  $('profile-note').textContent = 'Age and household status come from the supplied profile. Inferred categories remain hypotheses; scores are not calibrated probabilities.';
  const enabled = signals.personalization_consent.value === true;
  $('personalization-toggle').textContent = enabled ? 'Disable personalisation' : 'Enable demo personalisation';
  const available = Object.values(signals).filter((s) => s.availability === 'available').length;
  $('catalog-summary').textContent = `Catalogue: ${available} / ${Object.keys(signals).length} signals available · 4 metadata fields per signal`;
  $('catalog-signals').innerHTML = Object.entries(signals).map(([key, s]) => `<details class="catalog-row"><summary><span>${escapeHtml(s.label)}</span><small>${escapeHtml(s.availability)} · ${statusLabels[s.status] || s.status}</small></summary><pre>${escapeHtml(JSON.stringify({ signal: key, ...s }, null, 2))}</pre></details>`).join('');
  if (!$('profile-form').contains(document.activeElement)) {
    $('profile-employment').value = signals.employment_status.value || 'unknown';
    $('profile-household').value = signals.household_status.value || 'unknown';
    $('profile-dependents').value = signals.dependents_count.value ?? '';
  }
}

async function load() {
  customers = await api('/customers');
  state = await api(`/customers/${selected}`);
  $('token-form').hidden = true;
  render();
}
$('customers').addEventListener('click', (event) => {
  const button = event.target.closest('[data-customer]');
  if (button) perform(async () => { const id = Number(button.dataset.customer); const next = await api(`/customers/${id}`); selected = id; state = next; $('profile-birth').value = ''; $('chat-input').value = ''; render(); });
});
$('simulate').addEventListener('click', () => perform(async () => { state = await api(`/customers/${selected}/simulate`, 'POST', { months: Number($('simulation-months').value), expected_as_of: state.analysis_date }); render(); }));
$('reset').addEventListener('click', () => perform(async () => { state = await api(`/customers/${selected}/reset`, 'POST'); render(); }));
$('consent-toggle').addEventListener('click', () => perform(async () => {
  const consent = await api(`/consents/${encodeURIComponent(state.consent.consent_id)}?customer_id=${selected}`);
  consent.consentStatus = state.consent.status === 'valid' ? 'revokedByPsu' : 'valid';
  if (consent.consentStatus === 'valid') consent.validUntil = new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10);
  state = await api(`/customers/${selected}/consent`, 'POST', consent);
  render();
}));
$('personalization-toggle').addEventListener('click', () => perform(async () => {
  state = await api(`/customers/${selected}/personalization-consent`, 'POST', {
    value: state.customer_profile.signals.personalization_consent.value !== true,
    source: 'declared', observed_at: new Date().toISOString().slice(0, 10),
    valid_until: new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10),
  });
  render();
}));
$('profile-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const fact = (value) => ({ value, source: 'declared', observed_at: state.analysis_date });
  const attributes = { employment_status: fact($('profile-employment').value), household_status: fact($('profile-household').value) };
  if ($('profile-birth').value) attributes.date_of_birth = fact($('profile-birth').value);
  if ($('profile-dependents').value !== '') attributes.dependents_count = fact(Number($('profile-dependents').value));
  perform(async () => { state = await api(`/customers/${selected}/context`, 'POST', { attributes }); $('profile-birth').value = ''; render(); });
});
$('profile-categories').addEventListener('click', (event) => {
  const button = event.target.closest('[data-category]');
  if (button) perform(async () => { state = await api(`/customers/${selected}/categories/${encodeURIComponent(button.dataset.category)}/feedback`, 'POST', { status: button.dataset.response }); render(); });
});
$('personalization').addEventListener('click', (event) => {
  const feedback = event.target.closest('[data-feedback]');
  if (feedback) perform(async () => { state = await api(`/customers/${selected}/insights/${feedback.dataset.id}/feedback`, 'POST', { status: feedback.dataset.feedback }); render(); });
  const plan = event.target.closest('[data-plan]');
  if (plan) { const e = state.events.find((e) => e.id === Number(plan.dataset.plan)); $('plan-title').textContent = e.personalization.action; $('plan-copy').textContent = e.personalization.tip; $('plan-dialog').showModal(); }
});
$('close-dialog').addEventListener('click', () => $('plan-dialog').close());
$('chat-proposals').addEventListener('click', (event) => {
  const button = event.target.closest('[data-proposal]');
  if (button) perform(async () => { state = await api(`/customers/${selected}/chat`, 'POST', { proposal_id: button.dataset.proposal }); render(); $('chat-messages').scrollTop = $('chat-messages').scrollHeight; });
});
$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const message = $('chat-input').value.trim();
  if (message) perform(async () => { state = await api(`/customers/${selected}/chat`, 'POST', { message }); $('chat-input').value = ''; render(); $('chat-messages').scrollTop = $('chat-messages').scrollHeight; });
});
$('token-form').addEventListener('submit', (event) => { event.preventDefault(); token = $('token').value; sessionStorage.setItem('lifeflow-token', token); perform(load); });
perform(load);
setInterval(async () => {
  if (busy || refreshing || !state || document.visibilityState !== 'visible' || !$('token-form').hidden) return;
  refreshing = true;
  const requestRevision = revision;
  const customerId = selected;
  try {
    const next = await api(`/customers/${customerId}`);
    if (!busy && requestRevision === revision && customerId === selected && JSON.stringify(next) !== JSON.stringify(state)) {
      state = next;
      render();
    }
  } catch (error) {
    if (!busy && requestRevision === revision) { $('error').textContent = error.message; $('error').hidden = false; }
  } finally { refreshing = false; }
}, 5000);
