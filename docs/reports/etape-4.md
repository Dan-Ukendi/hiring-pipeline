# Étape 4 — phase 4 : envoi, preuves, journal

## Décision sur 4c (route `hosted_form`) : non construite

La condition posée par le prompt (« seulement si un bouclier généralisé n'existe pas ») n'est pas remplie, d'après les sondes déjà exécutées sur votre machine :

- **Lever** : hCaptcha chargé sur `/apply` (requêtes réelles vers `hcaptcha.com`). Terminal, `manual_only`.
- **Greenhouse, Ashby, Workable** : une mention de `recaptcha` ou `turnstile` dans le DOM sans widget confirmé. La règle retenue (« incertain = prudence ») les classe en `protection_uncertain`, bloquant, donc `manual_only`.
- Un Turnstile ou un reCAPTCHA invisible chargé au clic n'est pas détectable sans interaction, que le pipeline n'a pas le droit de déclencher.

Aucun template de formulaire n'a donc été écrit. Le code de politique accepte la route (`send.hosted_forms_enabled`, désactivé par défaut) et prévoit la rétention `new_template`, mais aucun transport ne la sert. **Le projet se recentre sur le courriel et la préparation de dossiers**, ce qui est cohérent avec la règle « sous 10 % d'automatisable, la phase 4 se réduit à ces deux usages ». Le chiffre réel (`apply-plan build --limit 100`) reste à produire sur votre machine ; s'il contredit cette lecture, 4c pourra être reprise template par template.

## 4a — politique et orchestrateur (simulation)

- `submission/policy.py` : table de règles pure, toutes évaluées et journalisées. `docs_ready` exige `package.status == ready` et `guard_summary.blocking == 0` et `l4_unsupported` sous le seuil ; un paquet `held` va en `needs_manual`, jamais en `awaiting_approval` ; un paquet `awaiting_review` attend. Fraîcheur du plan : âge de `probe_fetched_at`, concordance de `plan_revision` et de `offer_text_sha256` entre plan et paquet, et `form_hash` observé quand il est fourni, avec `hold_reason = plan_stale` et le rappel `apply-plan build --refresh`. `language_mismatch` ne déclenche pas sur l'anglais de repli (`downgrade`).
- `submission/orchestrator.py` : machine à états (13 états), clé employeur dégradée à `eligible` puis promue à `plan_ready` avec un alias, de sorte que le verrou voit les deux clés ; verrou employeur (14 jours, 3 sur 90 jours) appliqué à l'enfilage et de nouveau à l'envoi ; offre différée et jamais perdue (`available_from` dans l'événement, réactivée après le délai).
- Séquence d'écriture conservée : transaction A (bail, budget du jour, tentative, clé d'idempotence), transaction B (`submitted_unconfirmed`), appel réseau, transaction C. Les tests prouvent depuis une seconde connexion que l'état « peut-être parti » est déjà validé quand le transport est appelé, qu'une interruption en plein envoi laisse `submitted_unconfirmed` sans renvoi possible, et qu'un bail expiré devient « peut-être parti » et non « à renvoyer ».
- Invariants au niveau de la base : déclencheur qui interdit `submitted` sans preuve d'un envoi réel ; journal `application_event` en ajout seul (déclencheurs `BEFORE UPDATE/DELETE`) ; index unique sur la clé d'idempotence, sur `canonical_key` (états ouverts) et sur `reply_token`. Le découpeur de migrations de la phase 1 a été étendu pour accepter les blocs `CREATE TRIGGER ... END`.
- Garde-fous d'exécution : plafond journalier pris dans la transaction du bail, fenêtre 8 h – 19 h Bruxelles en semaine, espacement aléatoire de 90 à 420 s (source aléatoire et sommeil injectés), fichier `STOP`, disjoncteur (3 échecs par fournisseur, 5 consécutifs, 2 non confirmés, taux > 50 %, `auth_required` bloque le fournisseur de façon permanente).
- Triple verrou d'envoi (`--send`, `send.enabled`, `JOBPIPE_SEND=1`) ; la suite de tests force `JOBPIPE_SEND=0`.
- **Point de validation 4a** : `jobpipe submit run` produit un rapport HTML lisible (`planned-*.html`) avec destinataire, objet, corps, pièces jointes (nom, empreinte, taille), champs et provenance, « 0 bytes emitted ». Un exemple généré sur le profil fictif est dans `docs/reports/demo-planned-submissions.html`. Il n'existe que deux routes ouvertes (courriel, et formulaire hébergé désactivé), au lieu des quatre du prompt, puisque `ats_api` est fermée et `external` n'existe plus.

## 4b — route courriel

- `submission/smtp.py` : message avec `Reply-To` sous-adressé (`prenom+jeton@domaine`), `Message-ID`, pièces jointes, `.eml` exact conservé comme preuve ; erreurs classées (authentification, destinataire refusé, coupure pendant `DATA` = ambigu, connexion impossible = reprenable). Testé contre un serveur SMTP local (boucle locale).
- `submission/confirm.py` : relève IMAP en lecture seule (`BODY.PEEK`, `EXAMINE`), corrélation par jeton, puis `In-Reply-To`/`References`/identifiants embarqués dans les DSN, puis domaine de l'expéditeur ; DSN = `failed_permanent` `bounced` ; réponse = preuve `receipt_ack` ; 24 h sans DSN = `no_bounce_window`.
- Dossier par candidature (`dossiers/<date>-<employeur>-<offre>-<id>/dossier.html` autonome avec les PDF envoyés, l'annonce capturée, les champs, les preuves, l'historique), `jobpipe recall "<texte>"` (FTS5 sur employeur, alias, domaine, nom, courriel et téléphone du contact, intitulé ; test : cinq requêtes en moins de 2 s), `jobpipe export --job-search-log` (CSV).
- `jobpipe submit doctor` : SPF, DMARC, DKIM (sélecteurs courants) pour l'adresse d'envoi, et rappel du test de sous-adressage.
- **Premier envoi réel : non fait, à vous.** Aucun identifiant SMTP n'existe. Marche à suivre dans `a-faire-par-le-proprietaire.md`.

## Écarts

- Les classes de rétention `free_text_question`, `declaration_required` et `ungrounded_claim` du plan sont portées par la phase 3 (paquet `held`) ; `watchlist_employer` et `borderline_filter` sont remplacées par la revue obligatoire sur le tier A décidée par vous.
- Le journal des approbations humaines de la phase 3 (`package_approval_event`) reste une table distincte de `application_event` ; les deux sont ajout seul.
- `employer_contact` existe depuis la phase 2 (par offre) ; la phase 4 la lit au lieu de créer une table par employeur.
- Pas de PDF pour l'export de preuve d'activité (CSV seulement) ; la classification LLM des messages entrants n'est pas implémentée (dernier point de l'étape 5, le moins urgent).
