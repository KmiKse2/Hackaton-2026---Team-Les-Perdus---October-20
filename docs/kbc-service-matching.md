# KBC catalogue matching and navigation

## Audit and integration decisions

The current repository remains authoritative. `karim.zip` was inspected with
Python's ZIP reader; no application, scripts or credentials were extracted or
executed. Its four relevant files were reviewed:

| Reference component | Reuse / adaptation |
| --- | --- |
| `backend/data/kbc_ecosystems.json` | All 229 workbook records preserved in the existing `backend/data/ecosystem.json`, including original IDs, names and ecosystems in `source_records`. Nine overlaps merged into existing canonical IDs. |
| `backend/services/recommendations.py` | Structured intent/event families, explicit service-term associations, explainable ranking and null-destination behaviour adapted into `service_matching.py`. Its permissive recursive text search and ecosystem-only scoring were removed. |
| `backend/main.py` | The old generic `POST /api/recommendations/match` was deliberately not added: existing customer-scoped analysis, conversation and goal endpoints already supply authorised context. |
| `backend/tests/test_recommendations.py` | Housing, renovation and empty-context scenarios expanded into the current pytest suite, with privacy, preference, URL and frontend coverage. |

The existing `ecosystem.relevant_services` category filter overlapped with matching.
It now delegates to the same deterministic matcher used by Gemini enrichment and
goal panels. There is one catalogue and one matcher, no second AI system.
Banking detection rules, financial calculations, ADC setup, PostgreSQL schema and
Docker configuration are unchanged.

## Contract and matching

The provider's existing `AnalysisContent` and `Recommendation` schemas stay unchanged.
`ConversationOutput` adds optional `related_goal_ids` (default empty), validated
against the authorised active goals. Follow-up turns can therefore refer to an
existing goal without proposing a mutation. Validated selected service IDs also
resolve through the matcher when the model uses a generic intent. URLs never enter the model's output
contract. After reference validation, the backend adds `matched_services` to each
HTTP recommendation using `DisplayAnalysis` / `DisplayRecommendation`. Every
original field remains present. Cached Gemini responses are enriched against
current catalogue records and current service dismissals on each request.

Example **added** field (abbreviated; not an offer):

```json
{
  "id": "rec_existing_stable_id",
  "goal_id": 1,
  "matched_services": [{
    "id": "kbc_myhome",
    "name": "MyHome",
    "provider": "KBC",
    "ecosystem": "Housing",
    "matched_intent": "housing",
    "why_recommended": "For your confirmed housing project: ...",
    "requires_confirmation": false,
    "destination_url": "https://www.kbc.be/particuliers/fr/thema/myhome.html",
    "destination_status": "verified",
    "destination_available": true,
    "action_label": "Learn more",
    "integration_status": "not_connected"
  }]
}
```

Matching uses a linked active goal category first, then the validated need's
single hypothesis/category. Conversation proposals use structured goal categories
or the structured intent, always conditionally until customer confirmation.
Aliases include MOVING/housing, CAR/mobility, HOME_RENOVATION/renovation,
travelling/travel and entrepreneurship/business. Generic custom categories remain
valid goals; an unknown category produces no service instead of a prose guess.

Only reviewed `official_source` records with an explicit supported intent can
match. Verified source dates must not be in the future. Ordering weights favour
confirmed goals, specific tools, available destinations and an already validated
relevant service ID. They are **not** suitability probabilities. At most three
services accompany a recommendation/turn; the goal overview contains at most six.
The workbook's term mappings are retained as catalogue annotations for unverified
records but do not make them eligible.

Protection rules:

- Rejected hypotheses, protected/allowed categories and declined topics constrain
  matching, including aliases.
- Credit, investment, developer integrations and unreviewed products are excluded
  from automatic matching. A home goal never implies a loan or investment.
  Financial reserves and existing deterministic plans remain unchanged.
- Unconfirmed situations retain conditional wording. No insurance is matched
  from an uncertain hypothesis.
- Declared existing coverage suppresses additional insurance suggestions.
  Confirmed product ownership suppresses exact known IDs/names; a confirmed
  generic insurance product conservatively suppresses insurance matching because
  its detailed coverage is not known.
- Service dismissal persists in customer-scoped `LifeDecision` records, using
  `service:<catalogue-id>`. Other customers are unaffected. No new table is needed.
- A Gemini-supplied unknown service still fails existing reference validation;
  it cannot become a destination. No transaction or customer data is appended to
  external URLs.

## Catalogue and verified destinations

There are 230 canonical records after merging the 229 workbook references with
the 10 existing curated records (nine overlaps). Thirteen have official source
verification; twelve have a verified navigation destination. The other 217 are
workbook-only records, excluded from matching until reviewed. Source verification
and navigation verification are separate fields.

Destinations checked against official pages on 2026-10-20:

| Canonical service | Destination | Navigation status |
| --- | --- | --- |
| `kbc_myhome` | [KBC MyHome](https://www.kbc.be/particuliers/fr/thema/myhome.html) | Verified information page |
| `immoscoop` | [KBC MyHome](https://www.kbc.be/particuliers/fr/thema/myhome.html) | Verified KBC page describing the service, not direct property search |
| `setle` | [KBC MyHome](https://www.kbc.be/particuliers/fr/thema/myhome.html) | Verified KBC page describing renovation support |
| `kbc_mymobility` | [KBC MyMobility](https://www.kbc.be/particuliers/fr/themes/mymobility/produits/mymobility-dans-mobile.html) | Verified information page |
| `kbc-036` | [Monthly car costs](https://www.kbc.be/retail/en/thema/mymobility/product/monthly-car-expenses.html) | Verified information page |
| `kbc-125` | [Travel eSIM / Firsty](https://www.kbc.be/particulieren/nl/product/betalen/zelf-bankieren/met-je-smartphone/mobile/e-sim.html) | Verified information page, Dutch |
| `start_it_kbc` | [Start it @KBC](https://startit-x.com/en/accelerate/start-it-kbc) | Verified programme page; current application availability must be checked there |
| `kbc_pension_information` | [KBC savings FAQ](https://www.kbc.be/retail/en/products/payments/self-banking/on-your-smartphone/mobile/mobile-faq/savings.html) | Verified educational page |
| `kbc_travel_cover_information` | [KBC travel insurance](https://www.kbc.be/particuliers/fr/assurer/famille/assurancevoyage.html) | Verified information page; existing coverage rules apply |
| `kbc_home_loan_information` | [KBC mortgage information](https://www.kbc.be/retail/en/loans/home/mortgage-loan.html) | Verified, excluded from automatic matching |
| `kbc_investment_information` | [KBC investment plan information](https://www.kbc.be/retail/en/investments/investment-plan.html) | Verified, excluded from automatic matching |
| `impact_us_today` | Existing KBC press-release source retained | **No verified direct destination**; informational fallback |

The complete list of unavailable destinations is reproducible without network
calls:

```powershell
docker compose exec api python -c "from backend.services.ecosystem import catalogue; print('\n'.join(s['id'] + ' - ' + s['name'] for s in catalogue() if s['destination_status'] != 'verified'))"
```

No slug-derived URLs are used. Backend and Flutter require HTTPS, a reviewed
official hostname, no user information, no nonstandard port, no query string,
no fragment and no backslashes. There is no redirect endpoint accepting arbitrary
URLs. `url_launcher` opens the static destination only after the user presses
**Learn more**; it never receives customer data. Links can change later and
require catalogue maintenance. Some pages are French/Dutch although LifeFlow's
own card text is English.

## Endpoints and interface

- `POST /api/customers/{id}/ai-analysis`: additive per-card `matched_services`;
  existing consent, reference validation, caching and provider errors retained.
- `POST /api/customers/{id}/chat`: additive
  `chat.last_response.matched_services`, from the same matcher.
- Existing customer/`life` snapshots: enriched `life.services` and
  `life.dismissed_service_ids`.
- New `POST /api/customers/{id}/services/{service_id}/feedback`, using the existing
  decision payload (`dismissed`, `rejected`, `postponed`, `accepted`).
  Consent and customer scoping are checked; unknown/unverified service IDs return
  404. The UI uses `dismissed`.

`ServiceCards` is shared by proactive recommendation cards, the current
conversation turn and the existing goal service section. It displays the provider,
ecosystem, deterministic relevance explanation, uncertainty and either **Learn
more** or a missing-destination explanation. **Hide this service** persists the
customer's choice. Failed navigation shows an error. No purchase or financial
action is available.

Background polls do not call Gemini or relaunch links. Current conversation
enrichment is retained across unchanged polls and invalidated when relevant
context changes. As before, `last_response` is a current-session result: a full
browser reload retains saved conversation text/goals but not the transient
enrichment of the last turn. Saved goals still show their services after reload.

## Files

New: `backend/services/service_matching.py`,
`backend/tests/test_service_matching.py`, `frontend/lib/service_cards.dart`,
`frontend/test/service_cards_test.dart`, this document.

Updated: `backend/data/ecosystem.json`, `backend/life_schemas.py`,
`backend/main.py`, `backend/services/{ecosystem,conversation,life,prompt_builder}.py`,
`frontend/lib/{main,recommendations,life_panels}.dart`,
`frontend/pubspec.yaml`, `frontend/pubspec.lock`, `README.md`.

## Rebuild, checks and demo

Validation executed on 2026-10-20:

- Backend: **172 tests passed**, including 30 new matching/integration cases.
  Gemini is mocked in automated tests. One existing Starlette/httpx deprecation
  warning remains.
- Flutter: **19 tests passed**, including six new service-card, API, safe
  navigation, dismissal and polling cases. `flutter analyze`: **no issues**.
- `docker compose up --build -d`: successful release build; API, database and
  frontend all healthy. `git diff --check`: clean.
- Live Gemini acceptance inside the API container, using an isolated temporary
  SQLite database: proactive analysis, conversational housing proposal,
  conditional service cards, confirmation, saved budget/plan, enriched goal
  recommendations, persistent service dismissal, follow-up conversation and
  consent withdrawal all passed. Existing PostgreSQL data was not modified.
- Edge/Playwright against the Docker Flutter build at a 430px viewport: mocked
  analysis enrichment rendered; a customer click opened the real official MyHome
  URL in a separate tab without query parameters; the card survived background
  polls with one analysis request and no JavaScript errors. The mock was limited
  to analysis output; browser navigation and the deployed Flutter build were real.

```powershell
docker compose up --build -d
docker compose ps
docker compose exec api pytest backend/tests -q
cd frontend
flutter analyze
flutter test
```

Open **http://localhost:8080**. Existing ADC instructions in the README apply;
no new credentials, service-account keys or partner API access are needed.

1. Select a demo customer with banking and personalization consent.
2. In the existing chat, say: “I want to buy a house in five years. My deposit
   target is 30000 EUR. I prefer saving rather than borrowing.”
3. Review the conditional service cards. Confirm the proposed goal/preferences
   with **Confirm and save**; Gemini does not save them automatically.
4. Use **Refresh recommendations**. A related housing recommendation can include
   MyHome/Immoscoop. **Learn more** opens a verified official information page.
5. **Hide this service**. Refresh again; the hidden service stays excluded.
6. Car: declare a mobility goal. Check the running-cost/MyMobility tools, with no
   unsolicited car loan.
7. Renovation: use an explicit renovation goal or a supported uncertain hypothesis.
   Uncertain situations retain confirmation wording; Setle is relevant. No new
   renovation banking detector has been added.
8. Travel: declare a travel goal; review the eSIM information. Existing insurance
   suppresses duplicate cover suggestions.
9. Custom goal: use `cycling_race` or another unsupported category. Planning still
   works; no unrelated product is invented.
10. Withdraw personalization consent: AI and service feedback are blocked; saved
    data review/deletion and authorised deterministic features remain available.

External **website navigation works**; partner APIs, purchases, applications,
quotes, eligibility decisions and live prices remain **unavailable**.


Car insurance matching fix: an explicit `car insurance` (or exact supported
French equivalent) goal title can resolve the manual form's default `personal`
category for matching only; stored goals are not rewritten. Generic car purchases
do not imply an insurance need. The workbook entry `kbc-030` now has an official
verified destination: https://www.kbc.be/retail/en/insurance/vehicle/car-insurance.html
(checked 2026-10-20). Confirmation, existing-cover and dismissal filters still apply.
