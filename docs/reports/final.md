# Rapport final — phases 2, 3 et 4

Branche `phases-2-4`, non fusionnée. Détail par étape : `etape-0.md` à `etape-5.md`. À faire de votre côté : `a-faire-par-le-proprietaire.md`. Rapports HTML générés par le pipeline sur le profil **fictif** : `demo-pipeline-overview.html` (rapport consolidé) et `demo-planned-submissions.html` (simulation de la file d'envoi).

## Ce que chaque phase fait

- **Phase 2** : pour chaque offre, route (`ApplyRoute`, huit valeurs), formulaire (champs, classes, types, visibilité, `accept` normalisé), obstacles (captcha, bouclier, authentification, multi-étapes, JavaScript, sélecteurs instables), faisabilité par une table de décision nommée, exigences de poste, langue, exigences de langue, faits employeur et contacts, `offer_text_sha256` et `form_hash` figés. Lecture seule sur le réseau, `robots.txt` respecté, 2 à 3 s par hôte.
- **Phase 3** : CV sélectionné et ordonné à partir de vos propres puces (aucun texte généré dans le CV), lettre en quatre blocs avec garde-fou L0 à L4, réponses déterministes aux questions, rendu docx puis PDF avec test de round-trip, paquets `ready` / `awaiting_review` / `held`, revue humaine configurable.
- **Phase 4** : file d'envoi avec machine à états et journal en ajout seul, clé d'idempotence, bail, verrou employeur, interrupteur, disjoncteur, simulation par défaut, route courriel (SMTP, preuves, passe de confirmation IMAP), dossiers HTML, `recall`, export, notification Telegram, relances en brouillon.

## Décisions prises en autonomie

1. **4c (formulaires hébergés) non construite**, parce que les protections mesurées ne permettent pas de conclure à leur absence (`etape-4.md`). La route reste branchée dans la politique mais désactivée.
2. **Protection incertaine = bloquante** (`protection_uncertain`) plutôt qu'ignorée.
3. **`auto_full` réservé au courriel**, avec un destinataire issu d'un lien `mailto:` ; une adresse déduite de texte libre reste `manual_only`. Le modèle `ApplyPlan` refuse toute autre combinaison.
4. **L'étage A du LLM est désactivé par défaut** (`--llm-plan` pour l'activer) tant que l'évaluation sur votre profil ne l'a pas justifié.
5. `job_id TEXT` comme identité unique ; couple de plan `(job_id, plan_revision)` ; migrations 004 (phase 2 et `run` partagé), 005 (phase 3), 006 (phase 4).
6. Interprétation de `language_mismatch` (l'anglais de repli avec `downgrade` est nominal ; une langue identique à l'offre ne déclenche rien).

## Vérifications

- Suite de tests complète hors ligne, `pyflakes` propre, les 495 tests de la phase 1 inchangés et verts (deux attentes de listes de migrations mises à jour, un test d'architecture assoupli pour autoriser le SQL dans `db/`).
- Garde réseau de session avec liste blanche de la boucle locale (le serveur SMTP de test et l'API de LibreOffice passent par là), `JOBPIPE_SEND=0` imposé pendant les tests.
- Invariants prouvés par test : aucun `auto_full` avec obstacle bloquant ou champ non résolu ; aucun `submitted` sans preuve (déclencheur SQL) ; journal immuable ; l'intention est validée comme « peut-être parti » avant tout octet ; un paquet `held` n'atteint jamais un clic d'approbation ; aucune sortie de modèle n'atteint la base sans modèle Pydantic validé.

## Ce que vous devez vérifier

1. Que le garde-fou est assez strict **et** assez souple sur votre vrai profil : les corpus adverse (31 cas) et propre (12) sont écrits contre le profil fictif. Il faudra y ajouter vos cas.
2. Le CV déterministe sur votre profil, à la main, avant toute génération par modèle.
3. Le chiffre réel d'automatisation (`apply-plan build --limit 100`) et la décision qui en découle.
4. La configuration réelle du courriel (SPF, DKIM, DMARC, sous-adressage) avant le premier envoi.
5. Les seuils de départ à mesurer après un mois : verrou employeur (14 jours, 3 sur 90), seuil `l4_unsupported_limit` (2), péremption des plans (7 jours).

## Écarts connus avec le prompt

- Pas de chiffre sur les offres réelles ni de lecture de la documentation Teamtailor (pas de réseau) ; Teamtailor est traité comme `unknown`.
- Corpus adverse de 31 cas au lieu de 40 à 60 ; pas de test de propriété Hypothesis (remplacé par des tests de mutation du profil).
- Classification LLM des messages entrants non implémentée ; export de preuve en CSV seulement ; rapport consolidé qui renvoie aux rapports détaillés de phase 1 et 2.
- La suite complète dure environ une minute grâce à un cache des conversions PDF (les tests de rendu appellent réellement LibreOffice).
