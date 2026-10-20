# LifeFlow 2.0 — implementation and demonstration

The initial audit is in [lifeflow-2-audit.md](lifeflow-2-audit.md).
This release extends the existing application; the banking Signal Engine,
PostgreSQL volume, ADC transport, Kate handoff and legacy dashboard remain.

## Requirements and status

| Specification / priority | Implementation | Verification / limits |
|---|---|---|
| Existing AI, P0 | Shared `generate_structured` Vertex SDK transport; existing `/ai-analysis` and `/chat` | ADC and provider error tests; no second provider |
| Progressive profile, P0 | Extend existing typed `CustomerContext.attributes`; housing, region, constraints, hobbies, passions, interests, ambitions, priorities, insurance, retirement | Edit/clear fields in My Information; existing employment/family/income fields retained |
| Preferences, P0/P1 | Persistent memory setting, saving/financing preference, communication, optional optimisation, protected/allowed topics, declined questions | UI and API; turning memory off removes history/proposals, not explicitly saved goals |
| Universal goals, P0 | Generic optional budget/date/motivation/category, multiple currencies, non-financial goals, primary goal, priority, pause/complete, revision checks | CRUD, cross-customer references, invalid input, reserves, concurrent allocation protection via customer lock |
| Dynamic questions, P0 | Shared instructions and current goals/facts, recent bounded history, 0–3 questions, declined topics | Mocked housing, retirement, hobby paths; model wording remains probabilistic |
| Structured memory, P0 | Existing facts plus existing ChatMessage; separate unconfirmed ChatProposal | Explicit confirm/reject, stale-context check, corrections/deletion, no generated assumption saved as a fact |
| Financial planning, P0 | Decimal zero-return plans: remaining, months, required contribution, gap, progress | 30,000 − 6,000 over 60 months = 400/month; 300/month gives 80 months |
| Multi-goal planning, P0/P2 | Shared savings pool minus reserve; each allocation counted once, currency separation; priority-based capacity distribution | No automatic bank allocation; paused/completed allocations stay reserved until edited/deleted |
| Recommendations, P0 | Goal reference, strict IDs, validated catalogue, stable cards; duplicate topics collapsed after validating all cards | Rejected cards filtered; identical context served from server cache; frontend state retained during scrolling |
| KBC catalogue, P0 | Ten sourced information records; JSON/XLSX importer with strict schema | Workbook missing; imported XLSX records are unverified, never fabricated APIs; sources below |
| Existing Flutter, P0 | Existing chat upgraded; proposals to confirm, My Goals, My Information, scenarios, service information | Widget tests for rendering, command submission, errors and narrow screens |
| Subscriptions, P1 | Reuses existing monthly grouping; tracks established price changes and annual candidate patterns in review service | Confirm frequency before confirmed annual cost; 39/month = 468/year; inferred projection separately labelled |
| Usefulness and voluntary optimisation, P1 | Unknown/regular/occasional/rare/unused/do-not-ask, importance, keep/review/consider-cancelling; optional goal association | No cancellation, no claim of non-use from transactions; potential savings never included as actual capacity |
| Progress, P1 | Persisted allocations/contributions, progress bar, deadlines, recalculated plans | Manual customer bookkeeping; no automatic transfers or investment performance feed |
| Triggers and notifications, P2 | New/incomplete/late/achieved goals; situation/income changes; subscription review/price change; supplied renewal dates | In-app only, persisted delivery IDs, cooldown, categories, snooze and dismissal |
| Insurance, P2 | Declared policy labels, scope, risks, validity; deterministic intersections | Possible overlap only. Actual exclusions, limits and suitability require insurer evidence |
| Advanced scenarios, P2 | Combined income-capacity reduction, unexpected expense and explicit hypothetical goal cost growth | Persisted scenario alongside base plan; never changes confirmed budget/goals; no invented interest/returns |
| Customer-first behavior | Shared core instructions and preference/service filtering; protected hobbies; existing cover checks; stability before investments | Investment information suppressed without declared reserve/capacity; no personalised investment execution |
| Consent/privacy | Consent before both model workflows, account scope, bounded/minimal context, no raw transactions, no names/IBANs from bank data | History/proposals cleared on bank scope changes or personalisation withdrawal; corrections cannot be resurrected from old chat |
| Customer control | Goal CRUD, hypothesis feedback, proposal confirmation, card reject/dismiss/snooze, memory deletion, conversation deletion, full LifeFlow erasure | Personal data review/deletion remains possible after withdrawal |
| Deployment/regressions | Same Compose services, same ADC read-only mount, additive tables | No credentials added; no volume reset required |

## Database changes

Seven additive SQLAlchemy tables:

- `life_goals`: customer-owned validated goal document, revision, timestamps.
- `life_settings`: validated customer preferences, budgets and declared policies.
- `life_plans`: most recent deterministic plan and optional hypothetical scenario.
- `chat_proposals`: unconfirmed structured suggestions with source/context fingerprint and review status.
- `subscription_reviews`: customer-owned recurring-payment fingerprint and declared review.
- `recommendation_cache`: context fingerprint and previously validated JSON.
- `life_decisions`: recommendation/notification decisions and delivery bookkeeping.

Profile facts and conversation messages reuse existing tables; no old columns are replaced.
`Base.metadata.create_all` creates missing tables at startup using the project's
existing migration strategy. Stop/restart the application after the build.
Do not delete the PostgreSQL volume. Existing JSON profile rows require no rewrite.
Future production deployment should introduce versioned migrations and backups;
this release does not pretend `create_all` is a general migration framework.

## API contracts

All customer routes retain the existing access dependency and scope every owned
record lookup to `customer_id`. These commands return the existing customer
snapshot, extended with `life`; clients consuming the old keys continue working.

| Method / customer suffix | Purpose |
|---|---|
| POST `ai-analysis` | Cached proactive validated recommendations (`automatic=true` additionally applies proactivity/cooldown) |
| POST `chat` | Existing chat; Gemini when configured; clearly labelled local preview if no project is configured |
| GET `life` | Goals, plan, preferences, subscriptions, proposals, service references and reminders |
| POST `goals` | Customer-confirmed goal creation |
| POST `goals/{id}` | Edit; `expected_revision` protects UI updates |
| POST `goals/{id}/delete` | Delete and release bookkeeping allocation |
| POST `preferences` | Validated settings/budgets/coverage |
| POST `plans/scenario` | Persist an explicit hypothetical scenario; confirmed budgets unchanged |
| POST `subscriptions/{fingerprint}` | Save declared frequency/usefulness/decision |
| POST `proposals/{id}/confirm` | `{accept: true/false}`; scoped, single-review and stale-context validation |
| POST `recommendations/{id}/feedback` | rejected/dismissed/postponed/accepted, optional postpone days |
| POST `memory/delete` | Remove profile facts, history and pending proposals |
| POST `chat/delete` | Remove history and pending proposals; keep goals/facts |
| POST `life/delete` | Remove all new personal records and remembered information; pause personalisation; retain bank records |
| POST `context` | Existing field-level correction/deletion; invalidate old history/proposals |

Proposals only create/edit customer information or goals. There are **no**
transaction, loan, purchase, investment, cancellation or partner-execution tools.

## Prompt and context

`prompt_builder.CORE_INSTRUCTIONS` holds shared behavioral safeguards:
understand the person, prioritise financial stability, respect passions, ask useful
questions, trust backend calculations, respect consent and rejected hypotheses.
The proactive instructions retain their old response contract plus optional
`goal_id`. `conversation.CHAT_INSTRUCTIONS` specialises the same core for intent,
questions and proposed goal/memory changes.

Both workflows use the same context builder and Vertex SDK transport with ADC.
They receive authorisation-scoped aggregates and signal evidence, current
hypotheses/feedback, selected confirmed information, goals, preferences, backend
plans and relevant catalogue entries. Only chat receives up to 12 recent
messages. Raw transactions, account numbers and customer names are not copied
from banking records. User-entered free text remains untrusted data.

Generated proposals are independently parsed into command schemas. Proposals do
not make any customer fact true. Confirmation checks a fingerprint of current
facts, preferences and goals and applies changes in one transaction.

## Catalogue sources and import

Verified on 2026-10-20, with limited factual descriptions and no prices or terms:

- [KBC MyHome, Immoscoop and Setle](https://www.kbc.be/particuliers/fr/thema/myhome.html).
- [KBC MyMobility](https://www.kbc.be/particuliers/fr/themes/mymobility/produits/mymobility-dans-mobile.html).
- [Start it @KBC](https://startit-x.com/en/accelerate/start-it-kbc).
- [KBC pension saving information](https://www.kbc.be/retail/en/products/payments/self-banking/on-your-smartphone/mobile/mobile-faq/savings.html).
- [KBC mortgage information](https://www.kbc.be/retail/en/loans/home/mortgage-loan.html).
- [KBC investment plan information](https://www.kbc.be/retail/en/investments/investment-plan.html).
- [KBC travel cover information](https://www.kbc.be/particuliers/fr/assurer/famille/assurancevoyage.html).
- [KBC housing release referencing Impact Us Today](https://www.kbc.com/content/dam/kbccom/doc/newsroom/pressreleases/2026/20260129%20MyHome_FR.pdf).

An `official_source` record means its limited description was checked, not that
eligibility, availability or a commercial offer was verified. All records have
`integration_status=not_connected`. Source links are curated, never generated.

```powershell
py -m backend.services.ecosystem KBC_Ecosystems.xlsx backend/data/imported-ecosystem.json
```

Workbook headers must match `ServiceRecord` fields; list columns contain JSON
arrays. The importer is an operator CLI, not an unprotected upload endpoint. XLSX
imports force `catalogue_only` and no verified URL. Review/verify records against
official sources before promoting them in the JSON catalogue. Use
`LIFEFLOW_CATALOGUE_PATH` for an alternative local path, or replace the built
catalogue and rebuild. The workbook itself was not provided.

## Product choices and limitations

- Default new-notification cooldown: 24 hours; low proactivity delivers at most
  one new reminder per batch, normal at most three. Off disables automatic
  generation in Flutter and notifications. Existing reminders remain in-app until
  dismissed or no longer applicable; no background push/email is sent.
- Default income/subscription increase thresholds: 10%; default renewal window:
  30 days. These are editable product choices, not statistical truths.
- Monthly candidates reuse 25–35-day recurrence; annual candidates use 330–400
  days. Both are hypotheses. Irregular, weekly and quarterly billing need a
  customer review; they are not falsely labelled monthly.
- Planning capacity is customer-declared. Observed income-minus-expenses is
  separately labelled indicative and never automatically allocated.
- Communication preference is recorded; branch/phone contact is not executed.
- The existing shared demo token protects a local demo, **not production
  customer identity**. Ownership checks prevent cross-customer object references,
  but real multi-user deployment needs bank/OIDC identity binding, session
  security and operational governance. No bank identity provider is supplied.
- No real Kate, partner, insurance-policy feed, payments, subscription
  cancellation, external notifications or regulated investment-suitability
  service is available. Safe information/review workflows are implemented;
  these external integrations remain blocked, not simulated as successful.
- Gemini may still fail, refuse or produce invalid JSON/references. The backend
  returns controlled errors and saves no proposed fact on failure. Tests verify
  contracts and safeguards, not every possible natural-language output.

## Demo journey

1. Start the application and open `http://localhost:8080`; Ctrl+F5 after a rebuild.
2. Select a synthetic customer and simulate a month to observe deterministic signals.
3. View Gemini recommendations and their supporting explanation. Use Later,
   Dismiss or Not relevant to record a decision.
4. In the existing chat, say: “I want to buy a house in five years. My own
   deposit target is 30,000 EUR, not the property price. I prefer saving.”
5. Answer relevant questions. Review the proposed goal/facts and select
   **Confirm and save**. Nothing is persisted as a confirmed goal before this.
6. In **My Information and Preferences → Preferences and financial limits**,
   declare a EUR savings pool of 9,000, protected reserve of 3,000 and monthly
   capacity of 300. These are demo inputs, not inferred balances.
7. Edit the goal: allocate 6,000, contribute 300/month, deadline exactly five
   years from today. The plan shows 24,000 remaining, 400/month required,
   a 100/month gap, and an 80-month alternative at 300/month.
8. Add a travel or retirement goal. Allocating the same 6,000 again is rejected.
   Review the priority-based shared-capacity scenario.
9. Expand relevant services. See official descriptions, source dates and
   `not_connected`; no offer/application is executed.
10. In My Subscriptions, confirm frequency and usefulness. The UI distinguishes
    projected costs from confirmed annual costs and potential savings.
11. Add declared insurance scopes/risks to review possible overlaps, or compare
    a stress scenario without changing the real goal.
12. Later ask the chat about the confirmed goal/preferences. Correct or delete
    facts; decline questions in preferences; withdraw personalisation consent.
    Both Gemini workflows then stop using the data.

## Commands and verification

```powershell
docker compose up --build -d
docker compose ps
py -m pytest -q
cd frontend
flutter test
flutter analyze
```

On this workstation the portable interpreter is
`.\.tools\python\python.exe`; its packages are installed with `.tools/pip.pyz`.
Tests use an isolated temporary SQLite database and mock Gemini. The Docker
application uses the existing PostgreSQL volume. Google credentials remain in
the existing read-only ADC mount, never in source or browser code.

Executed checks:

- Before implementation: **125 backend tests passed**.
- P0 integration checkpoint: **133 backend tests passed**.
- Final backend suite: **142 passed**. One existing Starlette/httpx deprecation
  warning; no failed tests.
- Final Flutter suite: **13 passed**; `flutter analyze`: **no issues**.
- `docker compose up --build -d`: successful web compilation and additive
  PostgreSQL startup; existing volume and ADC mount retained.
- Real Vertex/Gemini acceptance inside the Docker API image, using a temporary
  isolated SQLite database: three proactive cards, customer-declared home goal,
  explicit proposal confirmation, persisted 400/month plan, relevant official
  service references, a subsequent contextual conversation, then consent
  withdrawal blocking chat and proactive analysis. No production/demo PostgreSQL
  customer data was changed by this acceptance test.
- Automated unit/API tests mock Gemini; the live acceptance above explicitly
  used Vertex and existing ADC. They are different kinds of verification.
- Edge/Playwright against the served Flutter application at 430 × 932:
  real `/ai-analysis` returned 200; the goal editor accepted text and closed
  cleanly; background polling caused no repeat generation or browser error.
  The browser check did not save or alter any customer data. The screenshot
  review also identified and corrected the old local-preview chat label.

## Changed files

Verification scope is deliberately precise:

- **Implemented and tested:** the backend/API behaviors listed above, mocked
  Gemini contracts, core Flutter goal/recommendation rendering and mutations,
  real Gemini goal/confirmation/planning/recall flow, Docker serving and proxy.
- **Implemented, not individually exercised in a live browser:** every option
  in the preferences, subscription, coverage and stress-scenario editors.
  Their API behavior is tested and Flutter static analysis passes; a full
  browser matrix of all form combinations was not run.
- **Partial:** recurring-payment inference covers monthly/annual patterns,
  not every possible billing schedule; insurance checks use declarations,
  not independently retrieved policy documents; access remains demo-level.
- **Externally blocked / not implemented as live integrations:** missing KBC
  workbook import against the actual supplied file, partner/Kate execution,
  bank identity binding, policy retrieval, regulated suitability, external
  push/email delivery and commercial quoting. The safe local/importable parts
  are implemented. No integration success is fabricated.

Existing files extended:

`backend/main.py`, `backend/models.py`, `backend/context_schemas.py`,
`backend/ai_schemas.py`, `backend/requirements.txt`,
`backend/services/ai_analysis.py`, `backend/services/prompt_builder.py`,
`backend/services/solutions.py`, `backend/tests/test_ai_analysis.py`,
`frontend/lib/main.dart`, `frontend/lib/recommendations.dart`,
`dashboard/index.html`, `dashboard/app.js`, `compose.yaml`, `.env.example`,
`README.md`.

New files:

`backend/life_schemas.py`, `backend/services/life.py`,
`backend/services/conversation.py`, `backend/services/ecosystem.py`,
`backend/data/ecosystem.json`, `backend/tests/test_life.py`,
`frontend/lib/life_panels.dart`, `frontend/test/life_panels_test.dart`,
`docs/lifeflow-2-audit.md`, `docs/lifeflow-2-delivery.md`.

Local browser/real-provider smoke scripts and screenshots are under ignored
`.tools/`, not committed credentials or application fixtures.
