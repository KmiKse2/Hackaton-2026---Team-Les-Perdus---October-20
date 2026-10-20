# Monthly simulation and Kate proposals (V4)

The dashboard and Flutter web interface use English. Bank-provided transaction descriptions remain source data, rather than being rewritten or translated.

## Simulate several months

Select 1, 3, 6 or 12 months, then **Simulate next months**. Each click advances from the current analysis month. The API accepts any integer from 1 to 12:

```http
POST /api/customers/3/simulate
Content-Type: application/json

{"months":3,"expected_as_of":"2026-08-31"}
```

The expected date prevents a repeated request from advancing the same state twice. A stale request returns 409; reload before retrying. A batch is transactional. Existing booked and pending transactions are retained, new operations get stable month-specific IDs, and daily booked balance observations are generated from the preceding month-end balance. Amounts are synthetic EUR fixtures, not predictions.

Thomas receives regular salary and commuting payments; Sophie has rent, utilities and initial furniture expenses; Marc has a travel scenario every three months. All profiles also receive baseline monthly expenses. Signals are recomputed after every month. The monthly history shows the balance and events at each simulated month-end; it is not an interactive historical replay. More than one batch can be run, including across year boundaries.

The simulator requires the two authorised, enabled demo EUR accounts, complete history through the current month-end and a booked balance on that date. It refuses incompatible imported snapshots rather than silently replacing them. Other accounts are preserved and their existing coverage still determines whether combined metrics can be computed.

**Reset** restores the demo accounts to August 2026, clears generated future balance history, monthly summaries, chat and hypothesis feedback. It preserves consent settings and other imported accounts. The legacy no-body `/simulate` request remains an idempotent September scenario for compatibility.

Consent is checked against the real date, independently of simulated dates. Profile facts and category confirmations can expire as the simulation advances; the simulator never renews them silently.

## Proposals in the Kate chat

The response includes `chat` and `kate_context.solution_proposals` (handoff schema 2.0). Proposal rules are in `backend/services/solutions.py`:

| Situation | Options |
|---|---|
| Travel | Review travel cover, explore exchange rates, prepare a foreign cash request |
| Moving / home purchase | Review home insurance and prepare a moving budget |
| Employment / first-job change | Prepare a recurring income and expenses budget |
| Cash flow pressure | Review essential payments and seek adviser support; commercial suggestions are suppressed |
| Growing savings | Define a savings target and timeframe |

Unconfirmed situations use conditional wording. Refused or expired categories are excluded. Travel options ask about destination/currency instead of assuming that every trip requires currency exchange; insurance options start with existing cover. Quotes, eligibility, fees, rates and product availability are not invented.

Select a proposal or send a message:

```http
POST /api/customers/3/chat
Content-Type: application/json

{"proposal_id":"exchange_rates"}
```

Alternatively send `{"message":"USD 500, next Friday"}`. The local rule-based conversation records the request and explains the next step. It is not an LLM or a real Kate connection. Messages are persisted per customer, with the latest 100 available in the view. Messages attached to a proposal are hidden when that proposal is no longer available. Revoked access or personalisation consent blocks the entire personalised chat.

No external endpoint is configured and no rates, insurance or cash orders are executed. `integration=contract_only`, `sent_to_kate=false` and proposal `execution=preview_only` remain explicit. The user confirmed that no Kate API documentation is available; the structured proposals can be passed to a future adapter when KBC supplies its integration contract.
