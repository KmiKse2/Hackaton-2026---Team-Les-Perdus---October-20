# Profils et signaux V3

Le catalogue fourni est conservé dans `kbc_lifeflow_signal_catalog.txt`. Les 40 signaux sont exposés par `/api/signal-catalog` et calculés dans `/api/customers/{id}/profile`. Chaque signal porte `source`, `confidence`, `timestamp` et `status`, ainsi qu’une disponibilité et ses limites. Une source manquante produit une valeur inconnue, jamais un zéro implicite.

## Catégories

Les faits fournis permettent d’afficher la tranche d’âge, l’emploi, la situation familiale, les personnes à charge et le profil d’investissement. Ils ne sont pas déduits du nom ou des achats. Les tranches sont moins de 18 ans, 18–25, 26–35, 36–50, 51–65 et 66+ : cette dernière borne évite le chevauchement à 65 ans du catalogue.

Les catégories possibles sont étudiant, jeune actif, premier emploi, déménagement, achat immobilier, voyage, tension financière et épargne en hausse. Plusieurs peuvent coexister. Une hypothèse comporte ses indices et un score heuristique, pas une probabilité calibrée. Le client peut la confirmer ou la refuser ; ce retour est conservé 90 jours. Une déclaration explicite reste prioritaire. Un premier salaire observé ne prouve pas un premier emploi ; cette catégorie exige notamment un statut étudiant et un nouveau salaire récurrent.

## Sources et règles

- Les revenus et dépenses utilisent les opérations comptabilisées, hors transferts propres, et restent séparés par devise. Les comparaisons utilisent les trois derniers mois calendaires complets ; tous les comptes éligibles doivent avoir une couverture suffisante.
- Une récurrence exige un même compte, une même contrepartie et devise, des intervalles de 25–35 jours et une variation de montant limitée à 20 %. L’absence d’un salaire attendu tient compte d’une marge de sept jours ; elle ne signifie pas chômage.
- Le taux d’épargne compte une seule jambe des virements courant–épargne. Le seuil de retrait significatif de 500 unités monétaires est un choix de démonstration.
- La tendance d’épargne exige trois soldes de fin de mois ; la fréquence de découvert exige 30 observations quotidiennes consécutives. Un solde actuel isolé ne suffit pas. La projection de solde nécessite une couverture de 90 jours, un solde récent et des flux récurrents identifiables.
- Les anomalies comparent une dépense à au moins cinq dépenses historiques de même catégorie. Les seuils sont deux fois la moyenne, un écart de 100 unités et trois écarts-types lorsque calculables. Ce signal ne constitue pas une détection de fraude.
- Les produits, crédits, placements, journaux d’usage et échanges Kate viennent de blocs spécifiques. Sans inventaire complet et valide, l’absence de produit n’est pas affirmée. Le profil de risque est déclaré et daté, jamais inféré des dépenses.
- Le pays marchand facultatif `merchantCountry` et la devise alimentent l’activité étrangère, avec BE/EUR comme référence de démonstration.

## API complémentaire

`POST /api/customers/{id}/context` reçoit `attributes`, `products` et `usage`. Les attributs sont mis à jour individuellement ; une valeur `null` supprime l’attribut. Les blocs produits et usage fournis remplacent le bloc précédent. Les champs omis sont conservés. Les schémas complets sont dans `/docs`.

Exemple de corps pour modifier une situation familiale :

```json
{"attributes":{"household_status":{"value":"married","source":"declared","observed_at":"2026-09-30"}}}
```

`POST /api/customers/{id}/personalization-consent` reçoit par exemple `{"value":true,"source":"declared","observed_at":"2026-09-30","valid_until":"2026-12-29"}`. Ce consentement est distinct de l’accès bancaire : sa désactivation bloque profils, signaux et contexte personnalisé, tout en conservant l’affichage des comptes autorisés. Les nouveaux clients sont désactivés par défaut ; les trois profils fictifs ont un consentement explicite de démonstration.

`POST /api/customers/{id}/categories/{category}/feedback` reçoit `{"status":"confirmed"}` ou `{"status":"rejected"}`. Les faits déclarés se corrigent via `/context`, pas par cette route.

L’import bancaire accepte `balanceHistory`, une liste de soldes datés fusionnés par compte/type/date. Le snapshot courant reste dans `balances`. Le démarrage ajoute les tables sans effacer les transactions existantes.

Le tableau de bord permet de modifier le profil fictif, consulter les 40 signaux, répondre aux hypothèses et désactiver la personnalisation. Flutter affiche les catégories et leurs confirmations. Le contexte Kate inclut ce profil structuré ; aucun appel réel à Kate n’est effectué.
