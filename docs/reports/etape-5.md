# Étape 5 — observabilité et suivi

## Livré

| Besoin | Réalisation |
|---|---|
| Rapport HTML consolidé | `jobpipe dashboard` : entonnoir de bout en bout, ce qui attend le propriétaire (revues, approbations, applications à regarder, relances), raisons de blocage par étape, runs. Exemple sur données fictives : `docs/reports/demo-pipeline-overview.html` |
| Métrique de bout en bout | Entonnoir `découvertes → filtres → plan → automatisable → documents prêts → en file → envoyées → confirmées`, ramené à 100 offres découvertes. Le titre du rapport est « N sent and M confirmed per 100 offers discovered » |
| Dossiers par candidature, recherche plein texte, export | Étape 4b (`recall`, `export --job-search-log`) |
| Notification | `jobpipe notify` : résumé Telegram avec le chemin du rapport HTML (source de vérité), boutons « Approve and queue » / « Leave for later » pour les seules applications en `awaiting_approval`. `--poll` applique les approbations pressées. Un paquet `held` ne peut jamais être approuvé depuis Telegram. Pas de notification par courriel. Le jeton du bot est masqué dans les URL journalisées |
| Relances | `jobpipe followups` : brouillon `relance-draft.txt` à côté du dossier, sept jours sans réponse, jamais envoyé |
| Ordonnancement en deux temps | `jobpipe chain nightly` (phases 1 à 3, sans modèle de langage dans la chaîne) et `jobpipe chain day` (file d'envoi). La chaîne écrit un run parent dans la table `run` partagée ; une chaîne interrompue est marquée `interrupted` et la commande relancée reprend là où chaque phase s'était arrêtée, parce que chaque phase ne traite que ce qui n'a pas encore de résultat |
| Reprise après interruption | Test : un échec à l'étape 2 puis une relance donne `failed` puis `ok`, avec les étapes déjà faites non refaites par leurs sélections |
| Rétention et sauvegarde | `jobpipe backup` (copie en ligne de SQLite, rotation) et `jobpipe purge` (rapport par défaut, `--apply` pour supprimer) : pages archivées plus vieilles que `archive.retention_days`, runs de documents plus vieux que 60 jours qui ne sont ni prêts, ni en attente, ni envoyés. Les preuves (`data/artifacts`) ne sont jamais purgées |
| Évaluation | Une seule commande et un seul format pour les phases 2 à 4 : `jobpipe evaluate --phase apply|documents|submission --cases FILE`, cas `{case_id, phase, input, expected}`. Mesures : taux de faux `auto_full` (phase 2), `keyword_coverage` et `unsupported_keyword_rate` (phase 3), « envoyé alors que cela ne devait pas l'être » (phase 4) |

## Budget de calcul

Un seul modèle pour les trois phases (`ollama.model`, `qwen2.5:7b` par défaut), contexte plafonné à 4096 jetons dans les phases 2 à 4 (`apply.llm_context_limit`). La chaîne de nuit n'appelle aucun modèle par défaut : audit de phase 2 désactivé (`--no-audit`), génération de documents sans étage A, ce qui évite le rechargement du modèle entre phases. Les appels de modèle de la phase 3 (lettre, juge L4, réponses ouvertes) se déclenchent avec `jobpipe generate`.

## Non fait

- Classification LLM des messages entrants (`inbound_message.classification` reste NULL) : c'est la partie la moins urgente.
- Export de preuve d'activité au format PDF (CSV seulement).
- Le rapport consolidé ne reprend pas encore les sections détaillées des rapports de phase 1 et 2 ; il y renvoie.
