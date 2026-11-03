# Étape 0 — reconnaissance

Source des mesures : `docs/handoff/phase1-measures.md`, `schema.sql`, `probes.json` (machine du propriétaire, 2026-11-03). Les mesures d'environnement ci-dessous ont été complétées dans le conteneur de travail.

## Environnement de travail

| Élément | Constat |
|---|---|
| Réseau sortant | Bloqué par le proxy du conteneur pour tous les hôtes testés. Aucune sonde relancée. Travail sur fixtures. |
| Base réelle | Absente du dépôt. Le chiffre « `apply-plan build` sur ~100 offres » est à produire sur la machine du propriétaire (voir `a-faire-par-le-proprietaire.md`). |
| Python | 3.13 ici, 3.14 chez le propriétaire. |
| LibreOffice | Présent dans le conteneur (24.2.7.2), absent chez le propriétaire. |
| Ollama | Absent ici. Aucun test n'en dépend (cassettes). |
| Tests de départ | 495 verts, 11 désélectionnés (`network`). |

## Points des plans invalidés par les mesures

1. Identité `job.id` INTEGER : faux. Décision prise : `job.job_id TEXT` est l'identité unique. Le couple de plan est `(job_id TEXT, plan_revision INTEGER)`. Toutes les tables des phases 2 à 4 portent `job_id TEXT REFERENCES job(job_id)`.
2. `job.apply_path` : inexistant. La route faisant foi est `apply_plan.route`, dérivée de `job.apply_url` (jamais NULL, 0 sur 1077). La table de distribution de la phase 4 se calcule sur ce résultat.
3. Verdict de filtre `pass/borderline/reject` : le stock réel porte `filter_result.outcome` ∈ `pass`, `fail`, et `borderline` pour la séniorité seule. Statut d'offre écartée : `job.status = 'rejected'`. Les offres écartées restent en base (1058 sur 1077).
4. Score continu : aucun. Les tiers `a/b/c` sont dérivés par règle déterministe (module de contrat).
5. Langue : `job.language` existe sans confiance, NULL sur 430 lignes sur 1077. La phase 2 la lit ; lorsqu'elle est NULL ou non fiable, la phase 2 est l'unique détecteur et persiste valeur et confiance dans `offer_language`.
6. Texte de l'annonce : réécrit à la re-observation (`content_hash`). `offer_text_sha256` obligatoire, figé à la construction du plan.
7. Lecture seule : pas d'utilitaire générique. Un utilitaire générique par authorizer SQLite est construit en étape 1.
8. Table `run` : existe sans `phase`. Étendue par la migration 004.
9. Migrations : 004, 005, 006 réservées (phase 2, 3, 4). L'exécuteur découpe sur `;` : pas de triggers, pas de `;` dans les littéraux.
10. Le contexte Ollama configuré est 8192 ; le plafond des phases 2 à 4 est 4096, appliqué par un réglage dédié sans toucher à la phase 1.
11. Plan phase 2 : les routes `ats_api_submit` et `requires_credential → auto_full` sont supprimées (interdiction légale). `credential_kind` reste une étiquette.
12. Couverture : 625 offres sur 1077 viennent de Greenhouse. Le schéma de formulaire Greenhouse est lisible par l'API publique `?questions=true` (sonde 1 : 200 sur offre active).
13. Mesure 5 du prompt (distribution `apply_path`) : non applicable. Remplacée par la distribution de `apply_plan.route`, produite par `apply-plan build`.

## Sondes (déjà exécutées, non relancées)

Voir `docs/handoff/probes.json` et la conclusion dans `etape-2.md`. Résumé : lecture Greenhouse et Ashby sans clé disponible ; Lever sous hCaptcha (donc `manual_only`) ; Greenhouse, Ashby et Workable non concluants, traités avec prudence comme `manual_only` lorsqu'une protection est signalée sans widget confirmé.

## Contraintes d'architecture

`tests/test_architecture.py` interdit le SQL hors de `db/store.py`. Les nouvelles phases ont besoin de couches d'accès aux données dédiées ; le test est étendu pour autoriser le SQL dans le paquet `jobpipe/db/` uniquement (un module d'accès par phase), ce qui préserve l'intention (pas de SQL éparpillé dans la logique métier).
