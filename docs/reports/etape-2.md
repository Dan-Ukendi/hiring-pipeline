# Étape 2 — phase 2 : analyse de la page de candidature

## Conclusion en tête

**Le chiffre « `apply-plan build` sur une centaine d'offres réelles » n'a pas pu être produit ici** : le conteneur n'a ni base réelle ni accès réseau sortant. Aucun chiffre n'est fabriqué. La commande exacte à lancer sur votre machine est dans `a-faire-par-le-proprietaire.md`. Elle affiche `auto_full + auto_assisted: N of M (x%)` et avertit si le résultat est sous 10 %.

Ce que le code et les sondes déjà exécutées permettent d'anticiper, sans le présenter comme une mesure :

- **Lever** : hCaptcha chargé sur `/apply` (requêtes réelles vers `hcaptcha.com`). Détecté comme `captcha`, terminal, `manual_only`.
- **Greenhouse** (625 des 1077 offres) : le schéma de formulaire est lisible par l'API publique `?questions=true`, mais la page hébergée contient la chaîne `recaptcha` sans widget confirmé. Selon la règle retenue (incertain = prudence), `protection_uncertain` est bloquant : ces offres sortent en `manual_only`, sauf si la page ne contient aucune mention de ces fournisseurs.
- **Ashby, Workable** : non concluants, même traitement prudent.
- **Conséquence probable** : `auto_assisted` risque d'être très bas sur le volume actuel, puisque presque toutes les offres viennent d'ATS dont la page nomme un fournisseur de défi. Si le chiffre mesuré est sous 10 %, le prompt prévoit de réduire la phase 4 à la route courriel et à la préparation de dossiers. Ce constat est à confirmer par la mesure réelle, et la décision pour 4c est prise en tête de `etape-4.md`.

## Livré

| Composant | Fichier |
|---|---|
| Règles en données (hors code) | `config/apply/*.toml` : registre ATS, politique, schéma de profil, synonymes, sémantique, compétences |
| Registre ATS et agrégateurs | `apply/registry.py` |
| Client en lecture seule, robots.txt, politesse | `apply/fetch.py` (2 à 3 s avec gigue, un appel par hôte, `GET` seul, `MutatingVerbError` sinon) |
| Analyse de formulaire | `apply/forms.py` (champs, options, visibilité, `accept` normalisé, étapes) |
| Marquage sémantique et profil | `apply/semantics.py` |
| Obstacles | `apply/obstacles.py` (captcha, bouclier, auth, multi-étapes, JS, sélecteurs instables, type de fichier, fuite de jeton sans valeur) |
| Table de décision | `apply/feasibility.py` (lignes nommées, première correspondance gagne) |
| Fournisseur Greenhouse | `apply/vendors/greenhouse.py` (API publique en lecture) |
| Exigences, langue, faits, contacts | `apply/offer_analysis.py`, tables `offer_requirement`, `offer_language`, `offer_language_requirement`, `employer_fact`, `employer_contact` |
| Orchestration | `apply/builder.py`, `apply/service.py` (reprise, `--refresh`, run partagé) |
| Persistance | `db/apply_store.py`, migration 004 |
| Audit LLM consultatif | `apply/audit.py`, `db/apply_audit_store.py` (écriture limitée par authorizer) |
| Rapport HTML | `apply/report.py` |
| Commandes | `jobpipe apply-plan probe | archive | build | report | eval` |

## Corrections du prompt appliquées

- `ats_api_submit` : jamais émise (aucun fournisseur n'est « démontré ouvert »). `requires_credential` supprimé ; `credential_kind` n'est qu'une étiquette. Un test vérifie sur tout le corpus qu'aucun plan ne porte cette route.
- `offer_requirement` : règles déterministes sur liste fermée, offsets conservés, niveau `required`/`preferred`/`mentioned` par clause.
- `employer_fact` : extraits textuels de l'annonce avec `source_url` ; bloc omis quand rien n'est trouvé.
- `employer_contact` : nom, courriel, téléphone depuis l'annonce ou `mailto:`.
- Langue : `job.language` lue quand elle existe, détection par mots vides sinon, valeur et méthode persistées dans `offer_language`.
- `offer_text_sha256` figé dans `apply_plan` ; test qui prouve qu'une réécriture de l'annonce ne le modifie pas avant reconstruction.
- `is_hidden` sur `apply_field` : champs masqués conservés mais jamais obligatoires ni comptés.
- Plan partiel : traité via `feasibility`/`feasibility_reason_code` (`plan_partial`).
- `auto_assisted` : « la soumission requiert le pilotage d'un navigateur ».
- Sécurité du `auto_full` : réservé à `email_apply` avec destinataire résolu. Un destinataire déduit de texte libre (et non d'un lien `mailto:`) reste `manual_only` (« à confirmer ») pour tenir le taux de faux `auto_full` à zéro. Le modèle `ApplyPlan` refuse `auto_full` hors courriel.
- Protections : « widget chargé » (script, iframe, conteneur, clé de site) = `captcha`, terminal. « Chaîne présente sans widget » = `protection_uncertain`, également bloquant par prudence.

## Écarts avec le plan

- Les règles sont dans `config/apply/` et non dans `data/`, qui est ignoré par git.
- La tâche 3 du LLM (résumé de run en français) n'est pas implémentée : elle n'ajoute aucun fait et aucun chiffre, et le rapport HTML n'en a pas besoin. Les tâches 1 et 2 sont en place.
- Les fixtures de pages sont des pages **synthétiques** (`tests/apply_pages.py`), sauf mention contraire ; `apply-plan archive` capture des pages réelles sur votre machine.
- La commande `apply-plan eval` lit un JSONL `job_id, route, feasibility` ; l'unification du format d'évaluation des trois phases est faite à l'étape 5.

## Vérifications

595 tests verts hors ligne, `pyflakes` propre. Invariants de propriété sur le corpus de 24 cas : aucun `auto_full` avec obstacle bloquant ; aucun `auto_full` avec champ obligatoire non résolu ; `submit_endpoint` toujours présent dans la fixture servie ; seules des requêtes `GET`.
