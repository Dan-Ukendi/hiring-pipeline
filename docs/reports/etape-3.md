# Étape 3 — phase 3 : adaptation du CV et de la lettre

## Conclusion en tête

- **`profile.yaml` et `declarations.yaml` n'existent pas : c'est votre travail.** Tout ce qui suit a été construit et testé sur un **profil fictif** (`profile/profile.example.yaml`, persona « Alex Exemple »), dont chaque nom, employeur, chiffre et date est inventé. Aucun fait sur vous n'a été écrit. Les documents produits par les tests sont des démonstrations.
- **Le point de validation « l'étage A bat-il le pré-score ? » n'est pas tranché.** Il faut votre profil, un modèle local et des offres réelles. En attendant, le défaut est le pré-score déterministe (`jobpipe generate` sans `--llm-plan`), et l'étage A est opt-in. Si les chiffres de votre profil ne montrent pas de gain (`stage_a_beats_prescore`), l'étage A est à retirer.
- **Le CV adapté sans aucun appel LLM existe** (`jobpipe generate --no-llm`) : sélection et ordonnancement de vos puces mot pour mot. À relire sur votre vrai profil : s'il est mauvais, le problème est dans `profile.yaml`.

## Livré

| Composant | Fichier |
|---|---|
| Modèles du profil, chargeur, hash de contenu | `documents/profile.py` |
| Garde-fou L0 à L4 | `documents/guard.py` |
| Pré-score, plan déterministe, boucle de longueur | `documents/selection.py` |
| CV déterministe (une colonne, sans tableau) | `documents/cv.py` |
| Rendu docx puis PDF par LibreOffice, round-trip | `documents/render.py` |
| Routeur de langue | `documents/language.py` |
| Réponses déterministes et politique par catégorie | `documents/answers.py` |
| Étage A, lettre (étage B), juge L4, réponses ouvertes | `documents/stages.py` |
| Client LLM à cassettes et enregistreur | `documents/llm.py` |
| Orchestration et manifeste | `documents/package.py`, `documents/service.py` |
| Tables | migration 005, `db/documents_store.py` |
| Commandes | `jobpipe generate`, `jobpipe review list|approve|reject` |
| Évaluation à trois colonnes | `documents/evaluation.py` |

## Corrections du prompt appliquées

- Identité `job_id TEXT` partout ; couple `(job_id, plan_revision)` dans `generation_runs` et `application_packages`.
- `bundle_sha256`, `mime_type` et chemin absolu par fichier dans le manifeste ; le modèle refuse un manifeste dont le hash ne correspond pas aux fichiers.
- Statuts `draft | ready | awaiting_review | held | superseded` ; `approved_by` ∈ `policy | human`. Seule `DocumentsStore.approve` fait passer `awaiting_review` à `ready`, avec `approved_by = 'human'` ; un paquet `held` est refusé avec un message qui demande une nouvelle génération. Chaque décision est écrite dans `package_approval_event` (ajout seul), que la phase 4 importe dans son journal.
- Tiers dérivés par la règle du contrat (aucun score continu en phase 1). `review.required_tiers = ["a"]` configurable.
- `answers[].source` reporté dans le manifeste, énumération `declaration | computed | claims_bank | llm | manual`.
- Sévérités `blocking | degrading | informational` ; `guard_summary.l4_unsupported` exposé séparément, seuil `documents.l4_unsupported_limit` (2 par défaut) au-delà duquel le paquet est `held`.
- Répertoire `documents/<job_id>/<run_id>/` ; `outbox/` contient les fichiers finaux nommés `Nom_Prénom_CV_Employeur.pdf`.
- Néerlandais : jamais généré ; offre néerlandophone, ou langue peu fiable, ou exigence dure : documents en anglais avec `language_source = downgrade`, la rubrique langues ventile B2 / A2 par modalité ; exigence dure : `held` avec `nl_required`.
- Questions éliminatoires : diplôme, `held` sur tous les tiers ; seuil d'années, `held` sur le tier A et avertissement `EXPERIENCE_THRESHOLD_NOT_MET` sur B et C ; jamais de `skip`.
- Prétentions salariales : réponse automatique (milieu de fourchette) sur champ numérique, `held` sur champ texte libre.
- Isolation du LLM en écriture : tous les schémas de sortie sont `extra="forbid"` ; une sortie hors schéma devient `value = None` ; un identifiant inconnu invalide le plan ; aucun module ne contient `model_construct`. Tests : `test_model_output_reaches_storage_only_through_validated_models`, `test_unknown_stage_a_identifiers_never_reach_the_database`.
- Bloc `fit` omis quand `employer_fact` est vide (mesuré par `letters without a fit block`).

## Limites et écarts

- Adresse « Madame/Monsieur <Nom> » : le genre ne se déduit pas d'un nom ; le français garde `Madame, Monsieur,`, l'anglais utilise `Dear <Nom>,` quand un contact est nommé.
- Le corpus adverse compte 31 cas et le corpus propre 12 (écrits à la main contre le profil fictif), en deçà des 40 à 60 du plan. Le test de propriété par Hypothesis n'est pas écrit ; il est remplacé par le test de mutation du profil (retirer une métrique ou une compétence fait basculer un document en violation).
- Le résumé de CV généré par LLM (2 à 3 lignes) n'est pas implémenté : le CV est 100 % extrait de votre profil.
- L'estimation du nombre de pages est mesurée sur le PDF réel ; sans LibreOffice, `jobpipe generate` s'arrête avec la commande d'installation.
- Les alias de compétences ne sont pas encore réécrits dans le CV (seule la sélection est faite).

## Vérifications

Garde-fou : 31 cas adverses détectés avec les codes attendus, 12 cas propres sans violation bloquante, test de mutation du profil. Rendu : une page, ordre de lecture, accents, 300 Ko, détection d'un réordonnancement et d'un débordement. Hors ligne : cassettes pour tous les appels de modèle.
