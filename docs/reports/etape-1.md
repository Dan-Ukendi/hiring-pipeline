# Étape 1 — module de contrat partagé

Paquet `jobpipe.contract`, écrit seul avant toute phase. Aucun module de phase ne redéfinit ce qu'il porte.

## Contenu

| Module | Rôle |
|---|---|
| `enums.py` | `ApplyRoute` (8 valeurs de la phase 2), `Feasibility`, `ReasonCode`, `FieldClass` (5), `FieldType` (12), `Severity` (`blocking`/`degrading`/`informational`), `PackageStatus` (`draft`/`ready`/`awaiting_review`/`held`/`superseded`), `ApprovedBy` (`policy`/`human`), `AnswerSource` (`declaration`/`computed`/`claims_bank`/`llm`/`manual`), `Tier`, `HoldReason`, `RunPhase`, `RunStatus` |
| `models.py` | Modèles `extra="forbid", frozen=True` : `ApplyField`, `DocumentSlot`, `Obstacle`, `ApplyPlan`, `ApplyPlanBundle`, `PackageFile`, `PackageManifest`, `GuardSummary`, `OfferRequirement`, `OfferLanguageRequirement`, `EmployerFact`, `EmployerContact` |
| `identity.py` | Clé d'idempotence `sha256(canonical_key \| route \| destination \| bundle_sha256)`, clé employeur dégradée puis promue |
| `gate.py` | `documents_ready` (statut `ready` et `blocking == 0` et `l4_unsupported` sous seuil), `needs_manual`, `language_mismatch` |
| `tiers.py` | Règle déterministe des tiers (aucun score continu n'existe en phase 1) |
| `clock.py` | `Clock` injectable, `FixedClock`, conversions UTC ↔ Europe/Brussels, fenêtre d'envoi |
| `offline.py` | Garde réseau unique (socket, loopback autorisé) |
| `secrets.py` | Registre de secrets hors dépôt, permissions 600 imposées, `JOBPIPE_SEND` (0 par défaut) |

Hors paquet : `db/readonly.py` (connexion et portée en lecture seule par authorizer SQLite, réutilisables par les trois phases) et `db/run_ledger.py` (entité de run partagée).

## Décisions d'interface

- Identité de l'offre : `job_id TEXT`. Couple de plan : `(job_id, plan_revision)`.
- Identité du lot : `package_id`. `bundle_sha256` est le hash des `sha256` triés, et le manifeste refuse de se valider si le hash ne correspond pas aux fichiers. Chemin absolu et `mime_type` par fichier sont obligatoires.
- `ApplyPlan` n'accepte `auto_full` que par `email_apply` avec destinataire résolu : l'invariant est porté par le modèle, pas seulement par la table de décision. `requires_credential` disparaît ; `credential_kind` est une étiquette.
- Équivalence écrite une fois : questions d'employeur = `field_class == 'custom_question'` (`ApplyPlanBundle.custom_questions()`).
- `accept_types` : extensions en minuscules sans point (validé). Les types MIME sont dans `accept_mime`. `NULL` si l'attribut `accept` est absent.
- `ReasonCode` ajoute `protection_uncertain`, `aggregator_unresolved` et `plan_partial` à la liste de la phase 2, pour la décision sur les protections non concluantes et le traitement du plan partiel.
- `language_mismatch` : interprétation retenue du prompt. Pas d'écart si la langue des documents est celle de l'offre ; l'anglais avec `language_source = downgrade` est nominal ; tout autre écart déclenche. La lecture littérale (« ou si `language_source != downgrade` ») déclencherait aussi sur une langue identique, ce qui est absurde.
- Règle des tiers : `a` = employeur surveillé, filtres propres, audit non `drop` ; `c` = séniorité `borderline`, audit `borderline`/`drop`, ou route non résolue ; `b` = le reste ; aucun tier pour une offre écartée ou `closed`.
- `Run` : migration 004 ajoute `phase`, `parent_run_id`, `last_job_id` à la table existante (défaut `discovery`, la phase 1 n'est pas touchée). Les migrations 005 et 006 sont réservées aux phases 3 et 4.
- Garde réseau : fixture de session unique dans `tests/conftest.py`, avec `JOBPIPE_OFFLINE=1`, `JOBPIPE_SEND=0` et liste blanche loopback. Les cassettes LLM de la phase 3 complètent le dispositif.

## Écarts avec le prompt

- `tests/test_architecture.py` : le SQL est désormais autorisé dans tout le paquet `jobpipe/db/` (et non plus dans `store.py` seul), parce que chaque phase a besoin de son module d'accès. `Store.connection` est exposé en lecture pour ces modules.
- `tests/test_migration.py` : les attentes de migrations incluent 004.
- `tzdata` ajouté en dépendance conditionnelle Windows pour `Europe/Brussels`.

## Point de validation

`tests/test_contract.py::test_every_interface_type_compiles_together` construit un objet de chaque type d'interface sans erreur et le fait passer par un aller-retour JSON.

## État

536 tests verts hors ligne, `pyflakes` propre.
