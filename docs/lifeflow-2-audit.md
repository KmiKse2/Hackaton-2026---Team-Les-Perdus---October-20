# LifeFlow 2.0 audit and implementation ledger

Audit date: 2026-10-20. The repository, not the attachment's description of a
previous version, is authoritative. No ecosystem workbook was found.

## Before changes

| Specification | Existing implementation | Gap / planned extension |
|---|---|---|
| 1–2, 15: shared intelligence | `ai_analysis.py`: Vertex, ADC, strict JSON; `/ai-analysis`; deterministic Python engine | Share transport with existing `/chat`, which currently uses local templates, not Gemini |
| 3: progressive profile | `CustomerContext`, typed facts, 40 derived signals, consent | Hobbies, housing, constraints, preferences, editable information UI |
| 4: universal goals | One categorical `financial_goal` profile fact | Multiple persisted generic goals, allocation, priorities, deadlines, progress |
| 5: questions | Recommendation questions and fixed Kate prompts | Contextual chat questions, one to three, remembered answers and declined topics |
| 6: memory | Persistent `ChatMessage`, profile facts and hypothesis feedback | Confirm proposed structured changes, review/correct/delete, conversation deletion |
| 7: planning | Financial aggregates only | Decimal calculations, reserve, shared allocation, scenarios and persisted plans |
| 8: subscriptions | `customer_state.recurring`, `recurring_expenses` | Reuse monthly grouping; review, annualization, usefulness and decisions |
| 9: triggers | UI polls state; recommendations generated once per widget | Server context cache, preferences, cooldown, dismiss/snooze, in-app notifications |
| 10: customer first | Uncertainty, consent, financial-stress prioritization | Shared instructions, protected hobbies, voluntary optimization, cover review |
| 11: ecosystem | Local demo discussion catalogue; no partner API | Importable, sourced service catalogue; verified information distinct from execution |
| 12–13: prompt/output | Separate system/context; Pydantic schema and reference validation | Shared core behavior; goals, structured chat proposals, validated service references |
| 14: frontend | Flutter cards, local chat, timeline, feedback; legacy dashboard | Extend existing chat; goals, information, subscriptions, plans and notifications |
| 16: security | Consent/account scoping, optional shared demo token, no financial execution | Preserve controls, invalidate cached results, bounded input, safe deletion; demo token is not production identity |
| 17: tests A–L | Banking/profile/simulation/AI tests, Flutter recommendation tests | Goal, memory, chat, scenarios, subscriptions, triggers, consent, UI integration |
| 18–21: delivery | Docker/Postgres/ADC working | P0 then P1 then P2; additive schema, regression tests, demonstration and limitations |

## Architecture decisions

Retain `CustomerContext.attributes` as the single store for confirmed profile
facts. Retain `ChatMessage` and the existing chat endpoint/interface. Add generic
goal, preferences, pending proposal, subscription review, plan and recommendation
cache/decision tables. Generated proposals are not confirmed facts. A separate
explicit confirmation operation applies them. All customer-owned lookups include
customer ID. Customer locks serialize competing allocations.

Use the existing `Base.metadata.create_all` startup strategy for additive tables;
no existing columns or financial records need migration. No destructive reset of
PostgreSQL is required. Production identity, real banking/partner execution,
external notifications and regulated investment suitability are not available
in this demo and must not be simulated as working integrations.

Financial planning uses declared spendable savings and capacity, separately
labelled observed summaries, a protected reserve, and currency-specific budgets.
No returns, prices, taxes, rates or property deposits are invented.

Progress and final verification are recorded in `lifeflow-2-delivery.md`.
