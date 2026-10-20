# LifeFlow · Signal Engine pour Kate

PoC du challenge KBC par **Les Perdus**. LifeFlow transforme les champs de comptes, soldes et transactions en **observations explicables**, puis prépare un contexte structuré pour Kate. Un endpoint optionnel transmet désormais ce contexte minimisé à OpenAI pour produire une analyse JSON validée.

**Le schéma fourni sert de contrat d’adaptation local. Ce dépôt n’est ni un connecteur KBC certifié ni une intégration à une API Kate.** Toutes les données de démonstration sont fictives ; aucun appel bancaire ou envoi à Kate n’est effectué.

## Lancer

Sur le poste déjà préparé (Python portable dans `.tools/`, ignoré par Git) :

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

Ou, avec Docker Desktop fonctionnel :

```powershell
docker compose up --build -d
```

- Tableau de bord : http://localhost:8000
- Documentation des schémas et endpoints : http://localhost:8000/docs
- Santé : http://localhost:8000/health

Docker utilise PostgreSQL avec un volume persistant ; le mode Python local utilise SQLite par défaut. La configuration reste dans `.env.example`. La démo locale fonctionne sans clé ; seul le nouvel endpoint d’analyse GPT nécessite `OPENAI_API_KEY`.

Sur un nouveau poste (Python 3.11+) :

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Flutter web, dans un autre terminal :

```powershell
cd frontend
flutter pub get
flutter run -d chrome --web-port 5173 --dart-define=API_BASE_URL=http://localhost:8000
```

Sans Chrome : `flutter run -d web-server --web-port 5173`, puis ouvrir http://localhost:5173. Les cibles Android/iOS ne sont pas fournies ni validées ; Flutter web est la cible de démonstration.

## Profils et catalogue V3

### V4: English interface, monthly simulation and Kate chat

The dashboard and Flutter interface now use English. Select **1, 3, 6 or 12 months** to advance the demo and inspect monthly summaries. The **Chat with Kate** preview suggests situation-based options (travel insurance, exchange rates, foreign cash, housing and budgeting), keeps a conversation per customer and respects consent and dismissals. Proposals are included in the Kate handoff; no real Kate API, live quotes or purchases are connected.

See [simulation and chat documentation](docs/simulation-and-chat.md) for scenarios, APIs, reset behaviour and integration limits.

V4 checks: **68 backend tests passed**, Flutter analysis and web build passed, and Edge verified simulations, chat persistence, dismissal, mobile layout and Flutter chat. Tests select an isolated temporary database before importing the application and refuse to reset any non-test database.

Docker V4 startup was also verified with PostgreSQL 16: API health is OK and both services run successfully. The 31 restored demo transactions were copied into the previously empty PostgreSQL database; SQLite remains available separately. Background polling and an outdated API response were checked to ensure controls do not get stuck in the processing state.

If the dashboard reports an older API, restart the server and refresh the page. Background refresh no longer disables the controls; failed requests release the processing state. For a Docker `Internal Server Error`, first check `docker info`: on this workstation the cause was a broken Docker Desktop connection to its Linux engine (`no route to host`), resolved by restarting Docker Desktop and its `docker-desktop` WSL distribution. Use `docker context use desktop-linux`. Stop the local Python server before binding Docker to the same port 8000. Docker uses its own PostgreSQL volume; the local SQLite database remains a separate file.

Les **40 signaux du catalogue fourni** sont maintenant pris en charge, avec source, confiance, date, statut et disponibilité. Les profils distinguent les faits déclarés/KYC (âge, emploi, situation familiale) des catégories inférées à confirmer. Le consentement à la personnalisation est distinct de l’accès bancaire.

Le tableau de bord permet de modifier les profils fictifs et de consulter le catalogue ; Flutter affiche les catégories. Les sources absentes restent inconnues. Voir les [règles, sources et nouvelles routes](docs/customer-signals.md) et le [catalogue original](docs/kbc_lifeflow_signal_catalog.txt).

Validation V3 : **56 tests backend réussis**, analyse Flutter sans erreur et compilation web réussie. Contrôle Edge sur une base isolée : modification du profil, confirmation persistante, consentement séparé, catalogue, affichage mobile et profil Flutter. Les tests ajoutés couvrent notamment les catégories, consentements, périodes complètes, revenus, transferts d’épargne, historiques de soldes et sources déclarées.

## Moteur bancaire hérité de V2

```text
Comptes + soldes + rapports booked/pending + consentement
                         ↓
Contrôle du périmètre et des permissions
                         ↓
Normalisation, exclusion des transferts propres et des comptes hors périmètre
                         ↓
Signaux : preuve, source, période, force de l’indice, limites
                         ↓
Hypothèses à confirmer + refus mémorisés
                         ↓
Contrat JSON pour un futur adaptateur Kate
```

Le moteur ne prend plus une catégorie de démonstration comme une vérité. Il lit les codes et champs bancaires. Les messages du téléphone sont des **aperçus locaux fixes** permettant de montrer le parcours, pas des réponses générées par Kate.

### Données prises en charge

| Bloc | Traitement |
|---|---|
| AccountDetails | `resourceId`, identifiants du compte, produit, titulaire, devise, type, statut, BIC et usage sont conservés. L’analyse personnelle porte sur les comptes `enabled` et `PRIV`. |
| AccountBalance | Montant/devise, type, dates et présence éventuelle d’une limite de crédit sont conservés. Les types de soldes ne sont pas additionnés entre eux. |
| AccountReport | Champs fournis conservés, rapports `booked` et `pending` distincts, unicité par compte + `transactionId`. |
| AccountAccess | Statut, expiration, permissions, périmètre explicite des comptes et budget de synchronisation automatique. |

Les champs optionnels restent absents lorsqu’ils ne sont pas fournis ; aucun âge, emploi, lien familial ou statut matrimonial n’est déduit du nom du titulaire. Les identifiants `DEMO-...` des exemples ne sont pas des IBAN réels.

### Signaux actuels

| Signal | Preuve et règle | Limite |
|---|---|---|
| Nouveau salaire | Crédit `purposeCode=SALA`, absent de la période antérieure, au moins 60 jours d’historique déclaré complet | Nouveauté dans les données uniquement ; pas nécessairement un premier emploi |
| Salaire récurrent | Deux crédits SALA, même émetteur identifié, intervalle de 25–35 jours, variation ≤20 % | Pas de garantie de revenu futur |
| Nouveau loyer | Débit RENT nouveau dans l’historique couvert | Pas de déménagement déduit d’un seul loyer |
| Hausse du loyer | Deux débits RENT mensuels au même destinataire, augmentation ≥15 % | Une hausse ne prouve pas un déménagement |
| Prélèvement récurrent | Deux débits mensuels avec le même `mandateId` | Ne décrit pas la nature du contrat |
| Nouvelle énergie | Débit avec code d’énergie, absent de l’historique couvert | Ne prouve pas un nouveau contrat |
| Transport, mobilier, avion, hébergement | Correspondance textuelle sur contrepartie/communication | Indices faibles, jamais présentés comme une preuve structurée |
| Compte d’épargne | `cashAccountType=SVGS` | Ne prouve pas une capacité d’épargne |
| Solde comptabilisé négatif | Snapshot comptabilisé récent, sur compte courant | Ne suffit pas à conclure à une difficulté financière |

Les règles et seuils sont des heuristiques de PoC, non calibrées statistiquement. Les sources, IDs des opérations, mesures et limites sont consultables en ouvrant un signal dans le tableau de bord. `bankTransactionCode`, les communications structurées et `endToEndId` sont conservés pour la traçabilité ; ils ne sont pas interprétés comme des catégories de dépense à eux seuls.

### Prévenir les faux signaux

- Les opérations `pending` n’alimentent ni revenus/dépenses ni hypothèses. Leur nombre reste visible. Un passage `pending → booked` avec le même identifiant met à jour l’opération ; une rétrogradation est refusée.
- Les virements vers/depuis les identifiants des comptes propres autorisés sont exclus des revenus, dépenses et signaux de vie. Un compte propre inconnu du périmètre ne peut pas être reconnu comme tel.
- Les devises sont calculées séparément, sans conversion ni somme EUR + USD. La carte principale affiche EUR lorsqu’il est présent, sinon la première devise disponible.
- Le disponible vient de `interimAvailable`. Il n’est jamais recalculé en ajoutant les transactions au snapshot. La présence ou l’absence d’information sur le découvert est affichée.
- Le comptabilisé utilise le snapshot le plus récent parmi `interimBooked` et `closingBooked`, avec priorité à `interimBooked` à date égale. `expected` reste visible par compte, sans être assimilé au disponible.
- Un solde vieux de plus de 7 jours par rapport à `asOf` est ignoré dans les agrégats et signalé. Sans snapshot exploitable, l’interface affiche **Indisponible**, pas zéro. Les agrégats portent uniquement sur les soldes renseignés, frais et autorisés.
- Les hypothèses combinent des indices sur un **même compte**. Le système ne rapproche pas arbitrairement les activités de deux comptes.

## Consentement et dates

L’expiration du consentement est contrôlée par rapport à la **date UTC réelle** à chaque lecture et mutation. `validUntil` est fourni explicitement ; aucune durée réglementaire n’est présumée. Le consentement synthétique créé au premier démarrage dure 90 jours par choix de démonstration.

Une permission supprimée masque aussi les données déjà stockées. Un consentement expiré/révoqué empêche les nouveaux imports, masque les comptes, soldes, transactions, signaux et hypothèses, et bloque le contexte Kate. Les données ne sont pas effacées automatiquement ; une politique de conservation réelle reste à définir. L’analyse nécessite `accounts` + `transactions` ; les soldes exigent aussi `balances`.

`asOf` est une **date de rejeu explicite**, indépendante du consentement. La détection regarde les 30 derniers jours avant cette date. Les revenus et dépenses sont ceux du mois de cette date, hors mouvements internes. `historyFrom`/`historyTo` déclarent la couverture complète du rapport fourni ; moins de 60 jours ou une couverture n’atteignant pas `asOf` interdit de conclure à la nouveauté d’un paiement.

Le mode `automatic=true` réserve atomiquement une unité de budget `frequencyPerDay` par import réussi. Un échec annule cette réservation. Les lectures du cache par Flutter/tableau de bord ne consomment rien. Un import manuel est distinct. Ce budget porte sur les **synchronisations locales** : le futur connecteur devra comptabiliser les véritables appels amont selon le contrat bancaire. Aucun ordonnanceur ni appel bancaire automatique n’est implémenté.

## API et import

Toutes les routes sont préfixées par `/api`. Les routes de comptes et de consentement requièrent `?customer_id=1` pour choisir le profil dans cette démo et vérifier la cohérence client/ressource.

| Méthode | Chemin | Usage |
|---|---|---|
| GET | `/accounts?customer_id=1` | Liste des comptes autorisés |
| GET | `/accounts/{resourceId}?customer_id=1` | Détails d’un compte |
| GET | `/accounts/{resourceId}/balances?customer_id=1` | Snapshots de solde |
| GET | `/accounts/{resourceId}/transactions?customer_id=1` | Rapports booked/pending |
| GET | `/consents/{consentId}?customer_id=1` | Métadonnées du consentement |
| POST | `/customers/{id}/consent` | Modifier le consentement **de démonstration** |
| POST | `/customers/{id}/banking` | Importer des snapshots bancaires |
| GET | `/customers/{id}` | État du client et diagnostic des données |
| GET | `/customers/{id}/insights` | Signaux et hypothèses |
| GET | `/customers/{id}/kate-context` | Contrat d’entrée pour un futur adaptateur Kate |
| POST | `/customers/{id}/simulate` | Jouer septembre |
| POST | `/customers/{id}/reset` | Restaurer les deux comptes de démonstration |
| POST | `/customers/{id}/insights/{event_id}/feedback` | Confirmer/refuser une hypothèse |

Exemple complet : [docs/banking-import.example.json](docs/banking-import.example.json).

```powershell
Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/api/customers/1/banking' -ContentType 'application/json' -InFile 'docs/banking-import.example.json'
```

Un import doit référencer un consentement actif et des `accountResourceIds` autorisés. Il n’accorde pas de permissions à lui seul. Pour un nouveau compte fictif, ajouter son identifiant au consentement de démo avant l’import. Le rattachement d’un compte appartenant à un autre profil est refusé.

Les blocs `balances` et `transactions` sont facultatifs : omission = conservation du bloc existant ; bloc fourni = **remplacement complet pour ce compte**. Fournir un rapport paginé incomplet comme un rapport complet supprimerait les opérations omises : le futur connecteur doit assembler toutes les pages avant l’import. `transactionId` est obligatoire, même pour pending ; sans identifiant stable fourni par la source, le connecteur devra définir sa politique de rapprochement. `endToEndId` et `mandateId` ne sont pas traités comme des clés uniques.

Le corps est validé : devises cohérentes, dates couvertes, identifiants uniques, montants décimaux et types de soldes connus. Un import est transactionnel ; aucun changement partiel ne survit à un échec. Une date `asOf` antérieure à l’état courant est refusée, sauf via la réinitialisation explicite de la démo.

L’ancien `POST /transactions` reste un **outil de compatibilité pour fabriquer des données fictives**, marqué déprécié dans OpenAPI. Il traduit les anciennes catégories en champs synthétiques et ne constitue pas un import bancaire réel.

## GPT analysis (LifeFlow architecture, step 1)

`POST /api/customers/{customer_id}/ai-analysis` now adds a stateless pipeline:
existing banking analysis → minimal Prompt Builder → OpenAI Responses API →
strict Pydantic validation → JSON response. Existing Kate previews, endpoints,
database tables and frontend behaviour remain compatible. This endpoint does
not consume chat history, save generated results, or execute actions.

Install `backend/requirements.txt`, then configure `OPENAI_API_KEY` and
`OPENAI_MODEL` on the **backend**. The default model is `gpt-4.1-mini`; overrides
must support Responses and Structured Outputs. Copy `.env.example` to `.env`
for Docker Compose, which forwards these variables to the API container.
For local Python, export the variables in the shell or use
`uvicorn backend.main:app --env-file .env`. Never put the key in frontend
configuration, version control, or requests to this endpoint.

```powershell
# After starting the backend with OPENAI_API_KEY configured:
Invoke-RestMethod -Method Post `
  -Uri 'http://localhost:8000/api/customers/1/ai-analysis' `
  -Headers @{'X-Demo-Token' = $env:DEMO_API_TOKEN}
```

No request body is needed. The response contains `customer_id`, `summary`,
`detected_needs`, `recommendations`, `clarification_questions` and
`suggested_actions`. Recommendations include an ID, title, description,
`need_id`, `signal_refs`, nullable `service_id`, justification and
`requires_confirmation`. Actions always have `execution: "proposal_only"`.
The customer ID is attached by the backend, never sent to or trusted from GPT.
Signal references such as `signal_1` are local to this analysis, not database IDs.
The full response schema is available in `/docs`.

The Prompt Builder reuses `banking.inspect`, personalization consent checks,
`customer_state.compute_catalog` and `solutions.proposals`. Data sent to OpenAI:

- Scoped banking signal types, aggregate metrics, confidence, limitations and
  anonymized evidence references; aggregate financial summaries and coverage.
- Available structured profile facts (age band, employment, household, industry,
  declared goals, preferred channel and risk profile), with source/status.
- Current inferred and confirmed hypotheses, stored confirmations/rejections,
  missing fields, and the relevant local service discussion topics.

Names, customer/account/transaction identifiers, exact birth dates, raw
transactions, counterparties, remittances, unrestricted occupation text and
conversation/interaction history are excluded. Declarations are labelled as
source assertions rather than independently verified facts. Missing or expired
facts remain unknown. Banking permissions filter data before prompt construction;
without balance permission, bank balances are unavailable. Product/profile
context uses the existing separate personalization consent. The prototype does
not add a separate consent record for external AI processing.

The endpoint applies the existing `X-Demo-Token` check (when `DEMO_API_TOKEN`
is set), checks customer existence and that customer's current personalization
and banking consent, and scopes bank reads by both customer ownership and
authorized accounts. A path ID cannot override these checks. **The shared demo
token authorizes the presenter across all synthetic customers; it is not
per-customer authentication.** With no token, this remains an open local demo.
Production requires authenticated customer ownership checks, external-processing
consent/governance, request throttling and cost controls.

Requests use the official OpenAI SDK with Structured Outputs, `store=False`,
a 30-second SDK timeout, no automatic retries and a 4,000 output-token cap.
`store=False` disables Responses storage; it does not establish zero retention
for every provider processing/logging policy. Errors return sanitized messages:
401 invalid demo token, 403 missing consent/permissions, 404 unknown customer,
503 missing key or provider rate limit, 504 timeout, 502 other provider errors,
refusal, incomplete output, invalid schema or invalid references.

Validation checks IDs, evidence/service references, confirmation flags and
proposal-only actions. Prompt instructions prohibit unsupported facts, invented
offers and revival of rejected hypotheses. Schema/reference checks cannot
guarantee factual correctness of free-text explanations; generated guidance
still needs review. The catalogue contains **demo discussion topics, not
verified KBC partner offers, prices or eligibility**. This is an OpenAI
integration, not an official Kate/KBC connector.

Tests in `backend/tests/test_ai_analysis.py` mock OpenAI; no credentials, live
calls or paid requests are required. Run `python -m pytest backend/tests -q`
(or the bundled `.tools/python/python.exe`). Implementation follows the
[official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Kate

`backend/services/kate.py` définit un contrat JSON local : observations, force des indices, mesures, limites, hypothèses, confirmation nécessaire et refus mémorisés. Les noms, IBAN, mandats et communications brutes ne figurent pas dans ce contrat. Les références de comptes sont opaques.

`integration=contract_only` et `sent_to_kate=false` sont explicites. Il faudra la documentation d’intégration fournie par KBC pour brancher un véritable adaptateur. Aucune URL, authentification ou capacité de Kate n’est inventée dans le projet.

## Démonstration

1. Thomas : simuler septembre → SALA + transport → changement professionnel possible (70/100).
2. Sophie : RENT + mobilier + énergie → déménagement possible (75/100).
3. Marc : avion + hébergement → voyage possible (60/100, indices textuels).
4. Ouvrir les signaux, puis le contexte JSON préparé pour Kate.
5. Refuser une hypothèse côté client ; elle disparaît des hypothèses à proposer à Kate.
6. Révoquer le consentement de démo : les données sont masquées, y compris après actualisation. Le rétablir est une action explicite du présentateur.

La réinitialisation restaure les deux comptes synthétiques du profil, supprime leurs opérations importées et réinitialise les hypothèses. Les autres comptes éventuellement importés sont conservés. Elle ne réaccorde jamais un consentement révoqué.

## Structure et mise à niveau

```text
backend/schemas.py          DTO validés des champs bancaires fournis
backend/models.py           tables V1 + comptes/soldes/rapports/consentements V2
backend/bank_seed.py        exemples bancaires et import initial des données V1
backend/services/banking.py consentement, persistance et import atomique
backend/services/engine.py  normalisation, signaux et hypothèses
backend/services/kate.py    contexte minimal pour Kate + messages d’aperçu local
backend/main.py             API
dashboard/                 vue moteur, preuves, consentement, aperçu client
frontend/                  Flutter web
```

Le démarrage crée des **tables supplémentaires** et importe une fois les profils V1 vers le format bancaire, sans effacer les anciennes transactions. Les confirmations/refus existants sont conservés. Le bootstrap est idempotent. Ce mécanisme local ne remplace pas un outil de migrations pour une future production.

## Vérification et limites

```powershell
.\.tools\python\python.exe -m pytest backend/tests -q
# Ou : docker compose exec api pytest backend/tests -q
cd frontend
flutter analyze
flutter build web
```

La suite couvre les scénarios, la persistance du feedback, le consentement et ses permissions, les quotas, l’isolation des ressources, les virements propres, la récurrence, les soldes, les devises, la promotion pending/booked et l’atomicité des imports. Le runtime PostgreSQL reste à vérifier sur un moteur Docker fonctionnel ; SQLite est utilisé pour les tests locaux.

Vérification V2 réalisée : **33 tests backend réussis**, analyse Flutter sans erreur et compilation web réussie. Les trois scénarios, les preuves des signaux, les refus, la révocation/réactivation du consentement et l’affichage mobile ont été vérifiés dans Edge sur une base de test séparée. Le contrôle d’intégrité de la base locale après mise à niveau est correct et le nombre de transactions V1 est inchangé.

Il n’y a pas d’authentification bancaire : le présentateur peut sélectionner et administrer tous les profils fictifs. `DEMO_API_TOKEN` protège facultativement l’API de démonstration ; ce jeton partagé ne remplace pas l’identité et l’autorisation par client d’un système réel. Une intégration réelle nécessitera notamment un connecteur authentifié, la vérification des consentements à la source, une politique de conservation et un audit. Aucun passage à l’échelle de millions de clients n’a été mesuré.

Prototype indépendant, sans affiliation officielle à KBC.
