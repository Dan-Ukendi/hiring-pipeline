# jobpipe — Plan d'architecture de la phase 2 : analyse de la page de candidature

## 1. Rôle de la phase 2 et principe directeur

La phase 2 prend une offre déjà stockée par la phase 1 et produit un **plan de candidature** (`apply_plan`) : la description machine-exploitable de la manière exacte dont cette candidature peut être soumise. Elle ne rédige rien, elle ne soumet rien, elle ne modifie pas la table `job`.

Trois invariants structurent tout le reste.

**Invariant 1 — la phase 2 est en lecture seule sur le réseau.** Le client HTTP de la phase 2 n'expose que `GET` et `HEAD`. Aucun `POST`, aucun envoi de formulaire, aucun clic sur un bouton de soumission dans le navigateur piloté. C'est une garantie testée, pas une convention : un test vérifie que la méthode `post` est absente de l'interface du client et qu'un appel à un verbe mutant lève une exception.

**Invariant 2 — la phase 2 n'écrit jamais dans `job`.** Le champ `apply_path` de la phase 1 reste tel quel. La route affinée vit dans `apply_plan.route`, table propre à la phase 2. Cela rend la phase 2 rejouable à volonté, permet de comparer son verdict avec celui de la phase 1, et évite qu'un bug de la phase 2 corrompe les données de découverte.

**Invariant 3 — le déterminisme produit les faits, le LLM produit des avis.** Les tables `apply_plan`, `apply_field`, `apply_document_slot` et `apply_obstacle` sont alimentées exclusivement par du code déterministe. Le LLM écrit uniquement dans `apply_field_suggestion` et `apply_audit`, que les phases 3 et 4 ne lisent pas comme source de vérité.

---

## 2. État réel du paysage ATS en 2026 : ce que j'ai vérifié

J'ai cherché, pour chacun des huit ATS Tier A, s'il existe un point de terminaison de **soumission** accessible à un candidat. Je distingue trois niveaux : *documentation primaire de l'éditeur*, *source tierce*, *non vérifié*.

Je précise d'emblée une limite de ma vérification : l'environnement depuis lequel j'ai travaillé bloque les appels sortants vers les domaines d'API (`boards-api.greenhouse.io`, `api.ashbyhq.com`, `api.lever.co` ont tous renvoyé un refus au niveau du mandataire). Je n'ai donc **pas pu sonder un seul point de terminaison en direct**. Tout ce qui suit vient de la documentation, et la première chose à implémenter dans la phase 2 est précisément la sonde qui confirme ou contredit ces lectures.

### Greenhouse
- La Job Board API comporte un point de terminaison `POST: Submit application`, et celui-ci **exige une authentification par clé d'API**. Source : article d'aperçu des API du support Greenhouse, qui énonce littéralement que ce point de terminaison requiert une authentification et renvoie au guide d'authentification par clé. **Documentation primaire, confiance haute.**
- Cette clé est une Job Board API key émise par l'employeur dans son Dev Center. Un candidat ne peut pas l'obtenir. **Conclusion : pas de route API pour Dan.**
- L'intégration sur site carrière passe par une iframe plus une balise de script, avec le board token de l'employeur (article du support mis à jour le 19 février 2026). **Primaire, confiance haute.**
- La lecture des questions du formulaire via `GET /v1/boards/{board_token}/jobs/{job_id}?questions=true` : **non vérifiée**. La page de documentation développeur était inaccessible, et les sources tierces se contredisent (l'une qualifie la Job Board API de « public, read-only and unauthenticated », la même page mentionne par ailleurs un POST authentifié). C'est la sonde numéro un à écrire : si ce paramètre fonctionne sans clé, la phase 2 obtient gratuitement le schéma de formulaire le plus riche du marché pour le plus gros fournisseur.

### Lever
- La documentation développeur de Lever liste un point de terminaison « Apply to a posting » et un point de terminaison « Retrieve posting application questions ». L'authentification se fait « via basic auth by providing an API key as the username and leaving the password blank », avec également un support OAuth. Racine de l'API : `https://api.sandbox.lever.co/v1` en sandbox. **Primaire pour l'authentification, confiance haute ; le chemin exact du POST n'apparaissait pas dans la portion de page lisible, confiance moyenne sur le chemin.**
- La clé est celle du client Lever, c'est-à-dire l'employeur. **Conclusion : pas de route API pour Dan.**
- Le listing public `https://api.lever.co/v0/postings/{site}?mode=json` n'est attesté que par des sources tierces. **Confiance moyenne**, à confirmer par sonde — mais c'est la phase 1 qui s'en sert, pas la phase 2.

### Workable
- La création d'un candidat sur une offre exige le scope `w_candidates` et un jeton porteur. **Primaire (documentation Workable), confiance haute.**
- Le schéma est, en revanche, remarquablement complet et vaut d'être noté pour la phase 4 au cas où un jeton serait un jour disponible : corps avec un drapeau racine `sourced` (à `false` pour créer un candidat ayant *postulé*, qui reçoit le courriel de remerciement) et un objet `candidate` contenant `name` ou `firstname`/`lastname`, `email`, `headline`, `summary`, `address`, `phone`, `cover_letter`, `education_entries`, `experience_entries`, `answers`, `skills`, `tags`, `social_profiles`. CV soit par `resume_url`, soit par objet `resume` avec `name` et `data` encodé en base64 ; formats acceptés `.pdf`, `.doc`, `.docx`, `.rtf`. Réponses aux questions via un tableau `answers` dont chaque élément porte un `question_key` obtenu auprès de `/jobs/:shortcode/questions`, avec une clé de valeur dépendant du type : `body` pour texte libre et texte court, `checked` pour booléen, `choices` (tableau d'identifiants) pour choix multiple et liste déroulante, `date` en ISO 8601, `number` ou `value` pour numérique (la documentation est elle-même incohérente sur ce point), `file_url` ou objet `file` avec `name` et `data` en base64 pour les fichiers. **Primaire, confiance haute, sauf la clé numérique.**
- Le listing sans clé `apply.workable.com/api/v1/widget/accounts/<account>` est attesté par source tierce uniquement. **Confiance moyenne.**
- **Conclusion : pas de route API pour Dan.** À retenir malgré tout : la structure `question_key` + type, c'est exactement le modèle de données que la phase 2 doit produire, quelle que soit la route.

### Ashby
- `POST https://api.ashbyhq.com/applicationForm.submit` existe, avec `jobPostingId` (uuid, requis), `applicationForm` (objet requis contenant les soumissions de champs), `utmData`, `tagIds`, `allowSubmissionForUnpublishedJobPosting`. Il **exige la permission `candidatesWrite`** et le panneau de crédentiels affiche une authentification Basic en base64. **Primaire, confiance haute.**
- La définition du formulaire vient de `jobPosting.info`, qui renvoie un champ `applicationFormDefinition` et **exige la permission `jobsRead`**. **Primaire, confiance haute.**
- En revanche, `GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}?includeCompensation={true|false}` est documenté avec un exemple `curl` nu, sans en-tête d'authentification et sans mention d'une clé. **Primaire, confiance moyenne-haute sur le fait qu'il soit sans clé** (la page ne l'affirme pas explicitement). Ce point de terminaison renvoie notamment `jobs[].applyUrl`, utile en phase 2 pour normaliser l'URL de candidature.
- **Conclusion : découverte sans clé, schéma de formulaire et soumission fermés.**

### SmartRecruiters
C'est la plateforme la mieux conçue des huit pour un dépôt programmatique, et elle est quand même fermée.
- `GET /postings/:uuid/configuration` renvoie les questions de présélection et les politiques de confidentialité. `POST /postings/:uuid/candidates` crée la candidature. `GET /postings/:uuid/candidates/:candidateId/status` en suit l'état. **Primaire, confiance haute.**
- « All endpoints are protected » : clé d'API, clé d'API partenaire, ou OAuth 2.0 avec le scope `candidate_applications_manage`. La Public Posting API elle-même exige désormais une clé ou un jeton OAuth avec le scope `internal_postings_read`. **Primaire, confiance haute.**
- La documentation impose aussi des obligations d'affichage que la phase 4 devrait respecter si elle passait un jour par cette API : les questions dont `complianceType` vaut `DIVERSITY` doivent figurer sous toutes les autres en conservant l'ordre de l'API, un intitulé « Confidential Diversity Questions » doit les précéder, le Markdown des libellés doit être rendu, le texte de politique de confidentialité du client vient de `settings.consentSettings` et le statut de consentement ne peut valoir `true` que si le candidat a vu ce texte, et un libellé `aiSettings.aiDisclosureLabel` doit être affiché quand des fonctions d'IA traitent les données. **Primaire, confiance haute.**
- Limitation de débit : 8 appels concurrents, HTTP 429 au-delà, limites adaptatives selon la charge, délai d'expiration de requête recommandé à au moins 128 secondes. **Primaire, confiance haute.**
- **Conclusion : pas de route pour un individu.** La clé partenaire est délivrée à une organisation sous contrat, pas à un chercheur d'emploi.

### Recruitee
C'est le seul cas où une fenêtre est peut-être encore ouverte, et elle se referme.
- La Careers Site API couvre `/offers`, `/offers/:id` et `POST /offers/:offer_slug/candidates`. Elle exige un en-tête `X-Careers-Sites-Token: <token>`, et la documentation précise que « Calls without the authorization header will return the `401 Unauthorized` error », avec une **échéance fixée au 10 février 2027** pour l'envoi obligatoire du jeton. Le flux XML des offres et le widget d'emplois ne sont pas couverts par ce jeton. **Primaire (docs.recruitee.com), confiance haute.**
- Lecture franche de cette échéance : il est possible qu'aujourd'hui, en novembre 2026, un `POST` sans jeton passe encore. Construire la phase 4 sur ce comportement, c'est construire sur une route dont la date de péremption est écrite dans la documentation de l'éditeur. La phase 2 doit donc marquer ce fournisseur d'un `credential_kind = 'careers_site_token'` et d'un `submission_policy` à renseigner à la main, et la phase 4 ne doit pas en faire un pilier.

### Teamtailor
- Flux RSS public par société, sans jeton. API REST protégée par clés de scope Public / Internal / Admin créées par un administrateur de la société. Une surface partenaire « Direct Apply » avec points de terminaison d'interrogation et de dépôt, sécurisée par HMAC ou liste blanche d'adresses IP. Teamtailor recommande elle-même de rediriger les candidats vers les sites carrière. Limite de débit de 50 requêtes par 10 secondes par seau, avec en-têtes `X-Rate-Limit-*`. **Source tierce (100hires, éditeur concurrent citant la documentation Teamtailor), confiance moyenne.** À vérifier sur la documentation primaire avant toute décision.
- **Conclusion provisoire : pas de route candidat.**

### Personio
Le seul des huit dont j'aie pu lire la spécification OpenAPI publiée par l'éditeur lui-même.
- Chemins : `GET /xml` (flux du site carrière, paramètre `language` requis), `POST /v1/recruiting/applications`, `POST /v1/recruiting/applications/documents`, et `POST /recruiting/applicant` déprécié.
- Authentification : `Authorization: Bearer <token>` avec le jeton d'accès de la Recruiting API, trouvé dans Settings > API > Access Data, plus un en-tête **`X-Company-ID` requis** ; `X-Personio-Partner-ID` et `X-Personio-App-ID` optionnels.
- Corps en `application/json`, pas en multipart. Champs requis : `first_name`, `last_name`, `email`, `job_position_id`. Optionnels : `recruiting_channel_id`, `external_posting_id`, `message`, `application_date`, `phase`, `tags`, `files`, `attributes`.
- Documents : téléversement préalable sur `/v1/recruiting/applications/documents` en `multipart/form-data` avec un champ `file`, maximum 20 Mo ; la réponse fournit un `uuid` que l'on place dans le tableau `files`, chaque élément exigeant `uuid`, `original_filename` et `category` parmi `cv`, `cover-letter`, `employment-reference`, `certificate`, `work-sample`, `other`.
- Questions personnalisées : tableau `attributes`, chaque élément avec `id` et `value`. Les identifiants personnalisés ont la forme `custom_attribute_123` (visible comme « API name » dans les réglages). Identifiants système : `birthday`, `gender`, `location`, `phone`, `available_from`, `salary_expectations`. Les attributs de type OPTION doivent correspondre à une option prédéfinie, les dates sont en ISO 8601.
- **Primaire (dépôt `personio/api-docs`), confiance haute.**
- **Conclusion : pas de route candidat.** Et une règle dure à inscrire dans le code : certains employeurs exposent par négligence leur jeton Recruiting API dans le source de leur page carrière. **La phase 2 ne collecte aucun jeton trouvé dans une page, ne le stocke pas et ne le journalise pas.** S'en servir serait un accès non autorisé, pas une astuce d'intégration. Un détecteur `credential_leak_seen` peut signaler le fait dans le rapport, sans jamais enregistrer la valeur.

### Verdict de la revue

**Aucun des huit ATS n'offre de point de terminaison de soumission utilisable par un candidat.** Les huit routes documentées exigent sans exception une crédentielle délivrée à l'employeur ou à un partenaire sous contrat. La conséquence est structurelle, et il vaut mieux l'intégrer maintenant : *la phase 4 ne sera pas « soumettre via l'API de l'ATS ».* Elle sera, par ordre de fiabilité décroissante, (1) le courriel via SMTP, (2) un formulaire hébergé par l'ATS piloté par Playwright, (3) un formulaire de site employeur, cas par cas, (4) rien du tout pour les portails à compte.

---

## 3. Taxonomie des routes de candidature

L'énumération `ApplyRoute` compte huit valeurs. Chacune est détectable de façon déterministe et porte un degré d'automatisation distinct.

| `route` | Description | Détection | Automatisation |
|---|---|---|---|
| `ats_api_submit` | Point de terminaison de soumission de l'ATS, accessible | Fournisseur reconnu **et** crédentielle présente en configuration locale | Totale si la crédentielle existe. En pratique : jamais, aujourd'hui |
| `ats_hosted_form` | Formulaire HTML hébergé par l'ATS sur son propre domaine | Hôte reconnu dans `ats_registry.toml` et présence d'un `<form>` ou d'un conteneur de formulaire connu | Pilotable au navigateur, sous réserve d'obstacles |
| `employer_site_form` | Formulaire sur le domaine propre de l'employeur, ATS non identifié | `<form>` avec un champ fichier et des champs de contact, hôte inconnu | Pilotable, maintenance élevée, sélecteurs fragiles |
| `email_apply` | Candidature par courriel | `mailto:` dans l'URL de candidature ou dans la description, ou libellé de candidature pointant une adresse | **La seule route réellement automatisable de bout en bout** |
| `account_portal` | Portail exigeant la création d'un compte | Redirection vers un chemin d'authentification, 401/403, ou formulaire contenant un champ `password` | Manuel |
| `aggregator_redirect` | L'URL de candidature pointe vers un agrégateur qui redirigera encore | Hôte dans la liste des agrégateurs connus | À re-résoudre ; si la résolution échoue, manuel |
| `closed` | Offre expirée ou retirée | 404, 410, ou page portant un marqueur de clôture connu | Sans objet |
| `unknown` | Cascade épuisée sans conclusion | Défaut | Manuel |

Deux remarques franches sur ce tableau.

D'abord, `email_apply` est la route la plus automatisable, et c'est contre-intuitif : elle n'a ni CAPTCHA, ni bouclier anti-robot, ni sélecteur CSS à maintenir, ni condition d'utilisation d'un ATS à interpréter. Un courriel envoyé depuis la propre adresse de Dan, avec son propre CV, est une candidature ordinaire. C'est aussi la route la plus rare sur les offres tech en Belgique — j'y reviens à la section 11.

Ensuite, `account_portal` ne doit pas être combattu. Les grands employeurs belges utilisent massivement Workday, SAP SuccessFactors et Taleo, qui ne figurent dans aucun des deux tiers de la phase 1 et qui exigent tous un compte. Ces offres sont classées `manual_only` et listées comme telles dans le rapport, pour que Dan décide à la main s'il y consacre du temps.

---

## 4. Cascade de détection de la route

La détection est une cascade ordonnée, chaque étage enregistrant sa source de preuve (`EvidenceSource`) et son niveau de confiance (`verified` / `inferred` / `unknown`). Dès qu'un étage conclut, la cascade s'arrête et la source de preuve de cet étage est écrite dans `apply_plan.route_evidence`.

1. **Normalisation de l'URL d'entrée.** `job.apply_url` si disponible, sinon `job.url`. Détection immédiate du schéma `mailto:` → `email_apply`, sans appel réseau.
2. **Empreinte d'hôte.** `registry.py` charge `data/ats_registry.toml` et fait correspondre l'hôte et le gabarit de chemin à un fournisseur, un locataire (`ats_tenant` : board token, site, sous-domaine, nom du job board) et un identifiant externe de publication. Les gabarits vivent dans un fichier de données, de sorte qu'ajouter un fournisseur soit un changement de données et non de code.
3. **Sonde HTTP en lecture seule.** Un `GET` qui suit les redirections en enregistrant la chaîne complète (`redirect_chain` en JSON, avec statut et URL à chaque saut), le statut final, le type de contenu, un sous-ensemble d'en-têtes mis sur liste blanche et les cookies posés. L'URL finale est souvent plus informative que l'URL d'entrée : c'est là que se révèlent les `aggregator_redirect` et les `employer_site_form` derrière un raccourcisseur.
4. **Point de terminaison de métadonnées du fournisseur, quand il en publie un sans clé.** Un module par fournisseur sous `jobpipe/apply/vendors/`, avec une interface commune `fetch_form_spec(tenant, external_id) -> FormSpec | None`. C'est ici que vivent la tentative Greenhouse `?questions=true`, le `posting-api` d'Ashby et le widget Workable. Chacun de ces modules commence par une sonde qui vérifie la disponibilité sans clé et, en cas d'échec, renvoie `None` sans faire échouer le plan.
5. **Analyse du formulaire HTML statique.** `forms.py` extrait de la réponse non rendue : `action`, `method`, `enctype` du `<form>` ; pour chaque `input`, `select`, `textarea` : `name`, `type`, `required`, `maxlength`, `minlength`, `pattern`, `accept`, `multiple` ; les options de chaque `select` ; le libellé associé via `for`/`id`, puis `aria-label`, puis le texte de l'élément parent ; les sections et étapes via les `fieldset`/`legend` et les `h2`/`h3` ancêtres.
6. **Rendu navigateur, en repli uniquement.** Si l'analyse statique trouve moins de deux champs alors que la page dépasse une taille plancher, un rendu Playwright est tenté, et l'obstacle `js_required` est enregistré. L'écart entre le nombre de champs statiques et rendus est conservé : c'est la mesure objective de la dépendance au JavaScript.
7. **Repli courriel.** Si aucune des étapes précédentes n'a produit de formulaire, recherche d'une adresse de contact de candidature dans la description de l'offre, par une expression déterministe restreinte aux motifs de candidature (`apply`, `candidature`, `sollicitatie`, `cv`, `jobs@`, `recruitment@`, `hr@`) dans un voisinage de l'adresse.
8. **Échec assumé.** `route = 'unknown'`, `feasibility = 'manual_only'`, `feasibility_reason_code = 'route_undetermined'`. L'offre reste dans le rapport, visible, étiquetée. Elle n'est jamais supprimée — c'est la règle de la phase 1, et elle s'applique ici aussi.

### Politesse réseau et robots.txt

Un seul appel concurrent par hôte, délai minimal de 2 secondes avec gigue entre deux appels au même hôte, agent utilisateur identifiable avec une boîte de contact. `robots.txt` est consulté pour l'URL de candidature et le résultat écrit dans `apply_plan.robots_allowed`. **Si l'accès est interdit, la page n'est pas récupérée** : `route = 'unknown'`, `feasibility = 'manual_only'`, `feasibility_reason_code = 'robots_disallowed'`. Ce n'est pas un obstacle à contourner, c'est un refus à respecter.

---

## 5. Extraction des exigences de soumission

Le modèle de champ est unique et porte un discriminant `field_class`, plutôt que deux tables parallèles pour les champs standards et les questions d'employeur. Deux tables auraient forcé les phases 3 et 4 à faire une union sur deux schémas différents pour une seule question pratique : « que faut-il fournir ? ». Un seul modèle, un discriminant.

`field_class` prend cinq valeurs : `standard` (nom, courriel, téléphone), `custom_question` (question propre à l'employeur), `compliance` (égalité des chances, diversité, données réglementaires), `consent` (acceptation RGPD, politique de confidentialité), `document` (champ fichier, dupliqué dans `apply_document_slot` avec les détails de téléversement).

`field_type` prend douze valeurs : `text`, `textarea`, `email`, `phone`, `url`, `select`, `multiselect`, `boolean`, `date`, `number`, `file`, `consent`. Cette liste est l'union des types exprimables en HTML natif et des types de questions des ATS examinés (le jeu de Workable — texte libre, texte court, booléen, choix multiple, liste déroulante, date, numérique, fichier — s'y projette intégralement).

### Marquage sémantique déterministe

Chaque champ reçoit un `semantic_tag` issu d'une table de mots-clés dans `data/field_semantics.toml`, par correspondance sur le libellé normalisé en français, néerlandais et anglais. Valeurs : `work_authorization`, `sponsorship`, `salary_expectation`, `notice_period`, `language_level`, `years_experience`, `gdpr_consent`, `eeo`, `diversity`, `relocation`, `driving_licence`, `security_clearance`, ou `NULL`. **Ce marquage est fait par une table de données, pas par le LLM**, parce que la phase 4 s'en servira pour décider s'il faut arrêter et demander à Dan.

### Résolution vers le profil

`data/profile_schema.toml` déclare les clés canoniques auxquelles Dan sait répondre : `first_name`, `last_name`, `email`, `phone`, `city`, `country`, `linkedin_url`, `github_url`, `portfolio_url`, `work_authorization_be`, `requires_sponsorship`, `notice_period_days`, `salary_expectation_eur`, `languages`, `education_level`, `years_experience`. `data/field_synonyms.toml` fait correspondre les libellés observés à ces clés.

Pour chaque champ, `profile_key` est résolu, et `profile_key_source` enregistre comment : `exact` (correspondance de `field_key` sur une clé connue du fournisseur), `synonym` (table de synonymes), `type_default` (un unique champ fichier requis sur une offre sans autre champ fichier se résout en `resume`), ou `unresolved`. Un champ requis non résolu met `needs_human = 1` et alimente le compteur `apply_plan.unmapped_required_count`, qui est l'entrée directe du verdict de faisabilité.

Les libellés restés `unresolved` sont agrégés dans le rapport HTML par fréquence décroissante. C'est le carnet de commandes du fichier de synonymes : chaque libellé fréquent qu'on y ajoute à la main fait basculer des offres de `manual_only` vers `auto_assisted`. C'est le levier d'amélioration le plus rentable de la phase 2, et il est entièrement sous le contrôle de Dan, sans modèle à réentraîner.

### Fichiers attendus

`apply_document_slot` décrit chaque emplacement de document : `slot` parmi `resume`, `cover_letter`, `portfolio`, `transcript`, `other` ; `accept_types` en JSON (extensions et types MIME tels que déclarés par l'attribut `accept`, sans invention lorsque l'attribut est absent) ; `max_bytes` lorsqu'une limite est déclarée ; `upload_mechanism` parmi `multipart`, `base64_json`, `remote_url`, `unknown`. Quand l'attribut `accept` est absent, `accept_types` vaut `NULL` et non une liste devinée : la phase 4 enverra alors un PDF, le format le plus universellement accepté des quatre que Workable documente.

---

## 6. Détection des obstacles

Chaque détecteur est une règle nommée qui écrit dans `apply_obstacle` le `kind`, une `severity` (`blocking`, `degrading`, `informational`), le nom du détecteur et le `signal` littéral qui a déclenché la règle. Journaliser le signal est ce qui rend un faux positif diagnosticable sans relancer de requête.

| `kind` | Signaux | Sévérité |
|---|---|---|
| `auth_required` | 401, 403 sur l'URL de candidature ; redirection vers un chemin d'authentification (`/login`, `/signin`, `/account`, `/register`) ; présence d'un champ `type="password"` ; paramètres SAML ou OIDC dans la chaîne de redirection | `blocking` |
| `captcha` | `recaptcha`, `hcaptcha`, `turnstile` dans un `src` de script ou un attribut de clé de site ; conteneur de défi connu dans le DOM | `blocking` |
| `bot_shield` | en-tête `cf-mitigated` ; cookie `__cf_bm` ; corps de défi Cloudflare sur un 403 ; signatures Akamai, PerimeterX, DataDome | `blocking` |
| `rate_limit` | 429 ; en-tête `Retry-After` | `degrading` |
| `multi_step` | champ caché de type `step` ou `page` ; bouton de soumission libellé « Next », « Suivant », « Continue » ; absence de champ fichier au premier écran alors qu'un CV est exigé par le texte | `degrading` |
| `js_required` | écart positif entre le nombre de champs rendus et le nombre de champs statiques | `degrading` |
| `unstable_selector` | champ sans `name`, sans `id` stable, ou avec un identifiant portant une empreinte de compilation | `degrading` |
| `file_type_restricted` | `accept` excluant le PDF | `degrading` |
| `credential_leak_seen` | motif de jeton d'ATS dans le source de la page | `informational`, **valeur jamais enregistrée** |
| `tos_restriction` | `submission_policy = 'restricted'` dans `data/ats_policy.toml` | `blocking` |

Point à ne pas adoucir : **`captcha` et `bot_shield` sont terminaux.** La phase 2 les constate et classe l'offre en `manual_only`. Ni la phase 2 ni la phase 4 ne tentent de les résoudre, de les contourner, ni de recourir à un service de résolution. Ce n'est pas une question de difficulté technique : franchir un dispositif posé pour exclure les accès automatisés transforme une automatisation en zone grise en un accès non autorisé, et expose Dan vis-à-vis d'employeurs auprès desquels il postule. Le gain n'est pas proportionné au risque.

---

## 7. Verdict de faisabilité

`feasibility` prend quatre valeurs, calculées par une table de décision explicite dans `feasibility.py` — pas par une somme de scores, pour que chaque verdict soit reproductible et explicable en une ligne.

**`auto_full`** — soumission sans aucune intervention humaine. Conditions cumulatives :
- `route = 'email_apply'` avec un destinataire résolu, **ou** `route = 'ats_api_submit'` avec une crédentielle présente en configuration locale ;
- aucun obstacle de sévérité `blocking` ;
- `unmapped_required_count = 0` ;
- `submission_policy != 'restricted'`.

**`auto_assisted`** — la machine remplit, un humain confirme le dernier clic. Conditions :
- `route` dans (`ats_hosted_form`, `employer_site_form`) ;
- aucun obstacle `blocking` ;
- `unmapped_required_count <= 1` ;
- un rendu, statique ou Playwright, a produit un formulaire exploitable.

**`manual_only`** — tout le reste de ce qui est encore ouvert : `account_portal`, `unknown`, `aggregator_redirect` non résolu, présence d'un obstacle `blocking`, `unmapped_required_count >= 2`, ou `robots_allowed = 0`.

**`blocked`** — `route = 'closed'`, ou `submission_policy = 'restricted'`.

`feasibility_reason_code` est une énumération fermée (`route_undetermined`, `captcha_present`, `bot_shield_present`, `auth_required`, `unmapped_required_fields`, `robots_disallowed`, `posting_closed`, `policy_restricted`, `no_credential`, `ok`) et `feasibility_reason_detail` porte le texte libre qui précise. Les phases 3 et 4 branchent sur le code, le rapport affiche le détail.

### La métrique qui compte

Le harnais d'évaluation de la phase 2 mesure la matrice de confusion des routes et la précision/rappel de la faisabilité, mais **la métrique opératoire est le taux de faux `auto_full`** : la proportion d'offres déclarées automatisables sans intervention qui ne le sont pas. Son mode de défaillance est une vraie candidature envoyée de travers à un vrai employeur, qui ne se répare pas. Cette métrique doit être poussée à zéro, au prix assumé d'un rappel médiocre. Un `auto_assisted` à tort coûte trente secondes d'attention ; un `auto_full` à tort coûte une opportunité.

---

## 8. Contrat de données SQLite

Six tables nouvelles, une vue, ajoutées par une migration numérotée suivant le mécanisme existant de la phase 1. Aucune colonne de `job` n'est modifiée.

### `apply_plan` (une ligne par offre, clé primaire `job_id`)

`job_id` INTEGER PK REFERENCES `job(id)` · `canonical_key` TEXT NOT NULL · `plan_revision` INTEGER NOT NULL DEFAULT 1 · `ats_vendor` TEXT · `ats_tenant` TEXT · `ats_external_id` TEXT · `route` TEXT NOT NULL · `route_confidence` TEXT NOT NULL · `route_evidence` TEXT NOT NULL · `apply_url_input` TEXT · `apply_url_final` TEXT · `redirect_chain` TEXT · `http_status` INTEGER · `content_type` TEXT · `submit_method` TEXT · `submit_endpoint` TEXT · `submit_enctype` TEXT · `email_recipient` TEXT · `email_subject_template` TEXT · `requires_credential` INTEGER NOT NULL · `credential_kind` TEXT · `feasibility` TEXT NOT NULL · `feasibility_reason_code` TEXT NOT NULL · `feasibility_reason_detail` TEXT · `required_field_count` INTEGER NOT NULL · `unmapped_required_count` INTEGER NOT NULL · `renderer_used` TEXT NOT NULL · `robots_allowed` INTEGER · `submission_policy` TEXT NOT NULL · `form_hash` TEXT NOT NULL · `probe_fetched_at` TEXT NOT NULL · `built_at` TEXT NOT NULL

`form_hash` est l'empreinte de la structure de formulaire analysée. Elle sert à détecter la dérive : si une reconstruction produit un `form_hash` différent pour la même offre, le plan est obsolète et la phase 4 doit refuser de s'en servir tel quel.

### `apply_field`

`id` INTEGER PK · `job_id` INTEGER NOT NULL REFERENCES `apply_plan(job_id)` ON DELETE CASCADE · `ordinal` INTEGER NOT NULL · `field_key` TEXT NOT NULL · `label` TEXT · `field_class` TEXT NOT NULL · `field_type` TEXT NOT NULL · `is_required` INTEGER NOT NULL · `max_length` INTEGER · `min_length` INTEGER · `pattern` TEXT · `options` TEXT · `allows_multiple` INTEGER NOT NULL DEFAULT 0 · `accept_types` TEXT · `max_bytes` INTEGER · `profile_key` TEXT · `profile_key_source` TEXT NOT NULL · `needs_human` INTEGER NOT NULL · `semantic_tag` TEXT · `group_label` TEXT · `step_index` INTEGER · `evidence` TEXT NOT NULL

Index unique sur `(job_id, field_key)`.

### `apply_document_slot`

`id` INTEGER PK · `job_id` INTEGER NOT NULL REFERENCES `apply_plan(job_id)` ON DELETE CASCADE · `slot` TEXT NOT NULL · `field_key` TEXT NOT NULL · `is_required` INTEGER NOT NULL · `accept_types` TEXT · `max_bytes` INTEGER · `upload_mechanism` TEXT NOT NULL

### `apply_obstacle`

`id` INTEGER PK · `job_id` INTEGER NOT NULL REFERENCES `apply_plan(job_id)` ON DELETE CASCADE · `kind` TEXT NOT NULL · `severity` TEXT NOT NULL · `detector` TEXT NOT NULL · `signal` TEXT NOT NULL

### `apply_probe_log`

`id` INTEGER PK · `job_id` INTEGER REFERENCES `job(id)` · `url` TEXT NOT NULL · `method` TEXT NOT NULL · `status` INTEGER · `elapsed_ms` INTEGER · `response_headers` TEXT · `body_sha256` TEXT · `body_path` TEXT · `fetched_at` TEXT NOT NULL · `error` TEXT

`response_headers` ne stocke qu'un sous-ensemble d'en-têtes sur liste blanche, pour ne jamais conserver de cookie de session ni d'en-tête d'autorisation.

### `apply_field_suggestion` — écriture LLM, valeur consultative

`id` INTEGER PK · `job_id` INTEGER NOT NULL · `field_key` TEXT NOT NULL · `suggested_profile_key` TEXT · `suggested_semantic_tag` TEXT · `rationale` TEXT · `model` TEXT NOT NULL · `created_at` TEXT NOT NULL · `accepted` INTEGER NOT NULL DEFAULT 0

`accepted` ne passe à 1 que par une commande de promotion lancée par Dan, qui inscrit la correspondance dans `data/field_synonyms.toml`. Les phases 3 et 4 ne lisent jamais cette table.

### `apply_audit` — écriture LLM, constat consultatif

`id` INTEGER PK · `job_id` INTEGER · `scope` TEXT NOT NULL · `finding` TEXT NOT NULL · `note` TEXT · `audit_status` TEXT NOT NULL · `model` TEXT NOT NULL · `created_at` TEXT NOT NULL

### `v_apply_audit_input` — vue en lecture seule offerte à l'auditeur

`CREATE VIEW` sur la jointure `apply_plan` × `apply_field` restreinte aux colonnes factuelles, sans `feasibility` ni `feasibility_reason_code` (pour que l'auditeur ne se contente pas de recopier le verdict déterministe). La connexion de l'auditeur est ouverte en `file:...?mode=ro`, et un test vérifie qu'un `INSERT` depuis cette connexion lève `sqlite3.OperationalError` — même schéma de preuve que la phase 1.

### Modèles Pydantic

`ApplyRoute`, `Feasibility`, `FieldType`, `FieldClass`, `ObstacleKind`, `Severity`, `Confidence`, `EvidenceSource`, `UploadMechanism`, `SemanticTag`, `CredentialKind`, `SubmissionPolicy`, `ProfileKeySource`, `ReasonCode` comme `StrEnum`.

`ApplyField`, `DocumentSlot`, `Obstacle`, `ProbeRecord`, `FieldSuggestion`, `AuditFinding`, `ApplyPlan` comme modèles, plus l'agrégat de lecture `ApplyPlanBundle(plan: ApplyPlan, fields: list[ApplyField], document_slots: list[DocumentSlot], obstacles: list[Obstacle])`.

---

## 9. Organisation des modules

```
jobpipe/apply/
    models.py        enums + modeles Pydantic
    registry.py      hote/URL -> fournisseur, locataire, identifiant externe
    policy.py        lecture de data/ats_policy.toml
    fetch.py         client HTTP GET/HEAD seulement, politesse, robots
    render.py        protocole PageRenderer, PlaywrightRenderer, ArchivedRenderer
    routes.py        cascade de detection
    forms.py         analyse des formulaires HTML
    semantics.py     libelle -> profile_key et semantic_tag
    obstacles.py     regles de detection
    feasibility.py   table de decision
    vendors/         un module par fournisseur, interface fetch_form_spec
    store.py         acces SQLite
    audit.py         auditeur LLM sur la vue en lecture seule
    report.py        section du rapport HTML
    cli.py
data/
    ats_registry.toml
    ats_policy.toml
    field_synonyms.toml
    field_semantics.toml
    profile_schema.toml
migrations/
    00X_apply_plan.sql
```

### Commandes

- `jobpipe apply-plan build [--limit N] [--since DATE] [--vendor NAME] [--refresh] [--no-audit]` — construit ou reconstruit les plans pour les offres sélectionnées.
- `jobpipe apply-plan probe URL` — diagnostic d'une seule URL en direct, affiche route, champs, obstacles et preuves, sans rien écrire en base. C'est l'outil de découverte à écrire en premier.
- `jobpipe apply-plan archive URL --out tests/fixtures/pages/...` — capture une page en fixture rejouable.
- `jobpipe apply-plan report --out reports/` — régénère la section de rapport.
- `jobpipe eval --export-unlabeled --stage apply` — exporte les plans d'un vrai run au format annotable.

---

## 10. Rôle du LLM local et frontière exacte

Le LLM local (Ollama, qwen2.5 en test initial) intervient après la construction déterministe, sur la vue en lecture seule, et seulement sur trois tâches.

**Tâche 1 — signaler un libellé que la table de synonymes a manqué.** Sortie contrainte : `field_key` existant + `suggested_profile_key` choisi dans l'énumération issue de `profile_schema.toml`, ou `null`. Écrit dans `apply_field_suggestion`. Aucune valeur libre n'est acceptée : une sortie hors énumération est rejetée à la validation Pydantic et enregistrée en `audit_status = 'unusable'`.

**Tâche 2 — signaler une question dont la formulation suggère un critère éliminatoire** (habilitation de sécurité, niveau de néerlandais exigé, années d'expérience au-delà d'un seuil). Sortie contrainte à l'énumération `finding` : `missing_required_field`, `label_unmatched`, `question_hard_blocker`, `route_mismatch`, `form_drift`, `none`, plus une `note` en texte libre bornée en longueur. Écrit dans `apply_audit`.

**Tâche 3 — rédiger en français le résumé de run affiché dans le rapport.** Ce résumé est présenté comme un commentaire, visuellement distinct des chiffres, et n'est jamais une source de chiffre : tous les nombres du rapport viennent de requêtes SQL sur les tables factuelles.

Ce que le LLM ne fait jamais : écrire dans `apply_plan`, `apply_field`, `apply_document_slot` ou `apply_obstacle` ; produire un `feasibility` ; produire une URL, un `submit_endpoint`, une adresse de courriel, une limite de taille ou un type de fichier ; décider qu'un champ est requis. Toutes ces valeurs ne peuvent venir que d'un signal observé et journalisé.

Un échec de parsage, un refus du modèle ou une absence d'instance Ollama n'interrompt pas le run : la ligne d'audit est écrite avec `audit_status = 'unusable'` et le run se poursuit. `--no-audit` saute entièrement cette moitié, et le run reste complet et exploitable par les phases 3 et 4 sans elle.

---

## 11. Stratégie de test hors-ligne

**Transport de fixtures.** Un `FixtureTransport` implémentant l'interface de transport de `httpx` sert les réponses depuis `tests/fixtures/pages/<vendor>/<case>/` : un `meta.json` (statut, en-têtes, URL finale, chaîne de redirection) et un `body.html` ou `body.json`. Aucune requête ne sort.

**Garde-fou réseau.** Une fixture pytest à portée de session remplace `socket.socket` par un objet qui lève immédiatement. Un cas de test dont la fixture manque échoue bruyamment au lieu de partir sur le réseau et de passer par accident. C'est ce qui rend crédible la promesse « testable hors-ligne ».

**Playwright hors du chemin de test.** Le rendu est derrière un protocole `PageRenderer`. L'implémentation de test est `ArchivedRenderer`, qui renvoie un `rendered.html` capturé une fois par `apply-plan archive`. Playwright n'est donc jamais lancé en test, et le navigateur n'est pas une dépendance de la suite.

**Corpus de fixtures à constituer**, par fournisseur et par cas limite : un formulaire hébergé par l'ATS ; une redirection vers un site employeur ; un portail à compte ; une page sous CAPTCHA ; une page sous bouclier Cloudflare ; une offre clôturée en 404 et une en 410 ; un `mailto:` ; une page sans champ en statique et riche en rendu ; un formulaire multi-étapes ; un formulaire avec question de type liste déroulante à options ; un formulaire avec consentement RGPD ; un formulaire sans attribut `accept` sur le champ fichier.

**Tests d'instantané.** Chaque fixture produit un `ApplyPlanBundle` sérialisé en JSON et comparé à un instantané de référence, avec un `--update-snapshots` pour les mises à jour volontaires. C'est ce qui attrape une régression de parsage sur un fournisseur qu'on ne touchait pas.

**Tests de propriété.** Trois invariants vérifiés sur tout le corpus : aucun plan ne porte `feasibility = 'auto_full'` avec un obstacle `blocking` ; aucun plan n'a `unmapped_required_count > 0` avec `auto_full` ; aucun `submit_endpoint` enregistré ne diffère d'une valeur présente dans la fixture servie.

**Test de la frontière d'audit.** Un test ouvre la connexion de l'auditeur, tente un `INSERT` sur `apply_field` et attend l'exception. Un second test fait tourner l'auditeur avec un client Ollama simulé renvoyant une sortie hors énumération, et vérifie que `apply_field` est inchangée et que la ligne d'audit porte `audit_status = 'unusable'`.

---

## 12. Observabilité dans le rapport HTML

La phase 2 ajoute une section au rapport de run existant. Tous les chiffres sortent de requêtes SQL, aucun du LLM.

- Répartition des offres par `route`, en barre, avec le compte absolu.
- Répartition par `feasibility`, et le chiffre mis en avant : le nombre d'offres `auto_full` et `auto_assisted`, parce que c'est le débit réel disponible pour la phase 4.
- Tableau croisé `ats_vendor` × `route`, qui révèle quel fournisseur redirige systématiquement ailleurs.
- Fréquence des obstacles par `kind`, avec le détecteur qui a déclenché.
- **Libellés `unresolved` les plus fréquents**, avec le nombre d'offres concernées et le gain de faisabilité attendu si on les résout. C'est le carnet de commandes actionnable du run.
- Distribution des `feasibility_reason_code`, qui dit en une lecture pourquoi les offres tombent en manuel.
- Détail par offre, dépliable : chaîne de redirection, source de preuve, moteur de rendu utilisé, latence de sonde, liste des champs avec leur `profile_key` et leur `semantic_tag`.
- Compteur de péremption : plans dont `probe_fetched_at` dépasse un seuil configuré, et plans dont le `form_hash` a changé depuis la dernière construction.
- Commentaire de run rédigé par le LLM, dans un encadré visuellement distinct des chiffres.

### Budget de temps

Pour 20 offres : 1 à 3 requêtes `GET` chacune avec 2 secondes de politesse par hôte, plus un rendu Playwright sur les offres qui en ont besoin, à 3 à 6 secondes par rendu sur le matériel de Dan. Soit de l'ordre de 3 à 8 minutes. L'audit LLM ajoute 20 appels courts, de l'ordre de 1,5 à 3 minutes sur une RTX 5050 de 8 Go avec un modèle 7B quantifié. La phase 2 coûte donc **5 à 11 minutes**, après les 10 à 16 minutes de la phase 1. Une chaîne complète approchera les 20 à 30 minutes par jour avant même d'ajouter les phases 3 et 4 : cela se planifie en tâche nocturne, pas en exécution interactive.

---

## 13. Points de franchise

**Aucune route API de soumission n'est accessible à Dan.** Les huit ATS examinés exigent sans exception une crédentielle d'employeur ou de partenaire. Ce n'est pas une lacune de ma recherche, c'est le modèle économique de ces plateformes : l'API de dépôt existe pour qu'un site carrière d'employeur ou un agrégateur sous contrat alimente l'ATS de cet employeur. La phase 4 ne peut pas être une phase « API ».

**La seule route entièrement automatisable est le courriel**, et c'est une minorité des offres tech en Belgique. Les PME et les ASBL y recourent encore ; les scale-ups et les grands groupes, qui sont le cœur de cible pour un poste junior IA/data, utilisent tous un ATS avec formulaire hébergé.

**Ordre de grandeur, à prendre comme estimation à vérifier dès la première semaine de runs.** Je n'ai pas de mesure : je n'ai pu sonder aucun point de terminaison en direct, et la distribution dépend du mélange de sources de la phase 1. Mon estimation raisonnée pour un junior IA/data à Bruxelles : 70 à 85 % des offres en `ats_hosted_form` ou `employer_site_form` ; moins de 10 % en `email_apply` ; 5 à 15 % en `account_portal`, essentiellement les grands employeurs sous Workday, SAP SuccessFactors ou Taleo, qui ne figurent dans aucun des deux tiers actuels. Donc **`auto_full` couvrira vraisemblablement moins de 15 % des offres au départ, et peut-être moins de 10 %.** La commande `apply-plan build` sur un vrai run de 100 offres donnera le chiffre exact en une soirée ; c'est le premier chiffre à produire avant d'investir dans la phase 4.

**Le livrable réaliste de la phase 4 est `auto_assisted`, pas la soumission non surveillée.** Un navigateur qui remplit un formulaire hébergé et s'arrête avant le dernier clic, Dan qui valide. C'est honnête, c'est robuste, et cela reste un gain de temps considérable par rapport au remplissage manuel. Mais il faut en voir le coût : à 20 offres par jour, c'est 20 confirmations par jour. Si l'objectif réel est de ne rien avoir à faire, le plafond de 20 est trop haut et il faut plutôt viser 5 à 8 offres très ciblées en `auto_assisted`, ce qui est de toute façon une meilleure stratégie de candidature.

**Conditions d'utilisation : je n'ai pas lu les conditions générales des huit éditeurs.** J'ai vérifié l'exigence technique d'accès, pas le droit contractuel. Deux nuances importantes. D'abord, les conditions d'un ATS lient en principe son client, c'est-à-dire l'employeur, et non le candidat qui remplit un formulaire public ; cette asymétrie est réelle mais n'est pas une autorisation. Ensuite, les conditions du site carrière de l'employeur lui-même, et celles des agrégateurs, restreignent couramment l'accès automatisé — raison de plus pour laquelle l'exclusion de LinkedIn, Indeed et Glassdoor est le bon choix. **Le fichier `data/ats_policy.toml` porte un champ `submission_policy` par fournisseur, à remplir par Dan après lecture, avec `unknown` par défaut. La phase 2 lit ce champ, elle ne le devine jamais.** Un fournisseur en `restricted` fait basculer ses offres en `blocked`, et la phase 4 ne les touche pas.

**Les employeurs commencent à filtrer les candidatures d'agents.** Un titre d'étude de Recruitics daté du 20 octobre 2026 annonce que plus d'un responsable d'acquisition de talents sur quatre bloquerait les agents IA candidatant à des offres — je n'ai pu lire que le titre, pas le corps, donc confiance limitée au titre. La conséquence opérationnelle est néanmoins à intégrer : une candidature peut être écartée en silence pour la façon dont elle a été envoyée, pas pour son contenu. La parade n'est pas de se dissimuler. Elle est de rester authentique : le CV de Dan, sa lettre, son adresse, son nom, un volume raisonnable. Un pipeline qui envoie 20 candidatures génériques par jour depuis une adresse jetable sera détecté et nuira à sa réputation ; un pipeline qui prépare 6 candidatures soignées qu'il relit est indétectable parce qu'il n'y a rien à détecter.

**Le contournement des protections est hors périmètre, définitivement.** Pas de résolution de CAPTCHA, pas d'empreinte de navigateur falsifiée, pas de rotation de mandataires, pas d'usage d'un jeton d'API trouvé dans le source d'une page. Chacune de ces techniques déplace le projet de l'automatisation vers l'accès non autorisé, et le fait dans le contexte précis où Dan a le plus à perdre : face à des employeurs auprès desquels il postule. Une offre protégée est une offre `manual_only`.

**Ce qu'il faut sonder avant de coder quoi que ce soit d'autre.** Dans cet ordre : (1) `GET /v1/boards/{token}/jobs/{id}?questions=true` chez Greenhouse, sans clé — si cela répond, c'est le plus gros gain de la phase 2 ; (2) la disponibilité sans clé du `posting-api` d'Ashby ; (3) l'état actuel d'un `POST` Recruitee sans jeton `X-Careers-Sites-Token`, pour savoir combien de temps il reste ; (4) la présence d'un bouclier Cloudflare sur les formulaires hébergés de Greenhouse, Lever, Workable et Ashby, parce que c'est ce qui décide si `auto_assisted` est atteignable sur la majorité du volume ; (5) la documentation primaire de Teamtailor, la seule que je n'aie pas pu atteindre.

---

## Hypothèses sur l'interface de phase 1

- La table des offres s'appelle `job`.
- `job.id` est une clé primaire entière utilisable comme clé étrangère depuis les tables de la phase 2.
- `job.canonical_key` existe, est de type texte, et porte la clé canonique inter-sources ajoutée par migration pour la déduplication.
- `job.apply_path` existe, est de type texte, et contient au moins les valeurs `external` et `unknown`.
- `job.apply_url` existe et contient l'URL de candidature quand la source la fournit ; la phase 2 suppose que cette colonne peut être nulle et retombe alors sur `job.url`. C'est l'hypothèse la plus risquée du lot : si cette colonne n'existe pas sous ce nom, la cascade de détection doit être amorcée autrement.
- `job.url` existe et contient l'URL de la page d'offre.
- `job.source` existe et nomme le module de découverte d'origine.
- `job.title`, `job.description`, `job.company`, `job.location` existent en texte.
- Une colonne porte la langue détectée de l'offre, avec `fr`, `en` et `nl` parmi ses valeurs.
- Le verdict de filtre est stocké avec les trois valeurs `pass`, `borderline`, `reject`, et une colonne distincte enregistre si c'est le titre ou la description qui a déclenché le verdict.
- `job` porte des horodatages de première et de dernière observation, en texte ISO 8601 UTC.
- Le chemin du fichier SQLite vient d'un objet de configuration unique, importable par la phase 2.
- Un exécuteur de migrations existe, avec une table de version, et accepte un nouveau fichier numéroté sans modification de son code.
- La phase 1 expose un utilitaire d'ouverture de connexion en lecture seule, construit pour sa couche d'audit, réutilisable tel quel par la phase 2.
- La convention du drapeau `--no-audit` et le répertoire de sortie du rapport HTML sont définis au niveau de la CLI racine et non par phase.
- Le sous-système `probe` expose un enregistrement de résultat par point de terminaison que la phase 2 peut étendre plutôt que dupliquer.
- Les offres restent en base quel que soit leur verdict de filtre : la phase 2 peut donc traiter des offres `borderline` et `reject` si Dan le demande, et le filtre ne restreint pas son périmètre d'entrée.

---

## Contrat de sortie pour la phase 3 et la phase 4

**Point d'entrée unique de lecture.** `jobpipe.apply.store.get_apply_plan(job_id: int) -> ApplyPlanBundle | None`, et `iter_apply_plans(feasibility: set[Feasibility] | None = None, route: set[ApplyRoute] | None = None) -> Iterator[ApplyPlanBundle]`. Les phases 3 et 4 ne font pas de SQL direct sur les tables de la phase 2.

**Garanties.**
- `apply_plan` est en relation un-à-un avec `job`, clé primaire `job_id`. Une reconstruction remplace la ligne et incrémente `plan_revision`.
- La phase 2 ne modifie aucune colonne de `job`. `job.apply_path` reste la valeur de la phase 1 ; la valeur affinée est `apply_plan.route`.
- Toute valeur de `apply_plan`, `apply_field`, `apply_document_slot` et `apply_obstacle` provient d'un signal observé et journalisé dans `apply_probe_log`. Aucune n'est produite par un modèle de langue.
- `apply_field_suggestion` et `apply_audit` sont consultatives. Les phases 3 et 4 ne les lisent pas.
- `form_hash` permet à la phase 4 de vérifier que le plan correspond encore au formulaire en ligne. Un plan dont `form_hash` diffère d'une reconstruction, ou dont `probe_fetched_at` dépasse le seuil de péremption, doit être reconstruit avant soumission.

**Ce que la phase 3 consomme.** `apply_field` filtré sur `field_class` dans (`standard`, `custom_question`) avec `is_required = 1` : la liste des réponses à produire, avec `label`, `field_type`, `max_length`, `options`, `semantic_tag`. Et `apply_document_slot` : quels documents générer, dans quels formats (`accept_types`), sous quelle limite de taille (`max_bytes`). La phase 3 sait ainsi qu'une lettre de motivation limitée à 2000 caractères dans un `textarea` n'est pas le même livrable qu'un PDF joint, avant de rédiger quoi que ce soit.

**Ce que la phase 4 consomme.** `route` et `feasibility` pour décider si elle agit ; `feasibility_reason_code` pour expliquer un refus dans la notification ; `submit_method`, `submit_endpoint`, `submit_enctype` pour la mécanique ; `email_recipient` et `email_subject_template` pour la route courriel ; `apply_url_final` comme point d'entrée du pilotage navigateur ; `apply_field` avec `field_key`, `profile_key` et `step_index` comme plan de remplissage ordonné ; `apply_document_slot` avec `upload_mechanism` pour l'attachement ; `apply_obstacle` pour s'arrêter avant d'essayer.

**Règle d'arrêt imposée à la phase 4.** La phase 4 ne soumet que sur `feasibility = 'auto_full'`. Sur `auto_assisted`, elle prépare et s'arrête avant le dernier clic, puis notifie. Sur `manual_only` et `blocked`, elle n'ouvre rien et se contente de journaliser. Cette règle est lisible dans une seule colonne, ce qui la rend vérifiable par un test de la phase 4 sans rejouer la phase 2.

---

## Risques et questions ouvertes

- **Le chiffre de faisabilité réelle n'est pas connu.** Mon estimation de moins de 15 % en `auto_full` est raisonnée, pas mesurée. Un run de `apply-plan build` sur une centaine d'offres déjà en base tranche la question en une soirée, et ce chiffre devrait précéder tout investissement dans la phase 4.
- **La disponibilité sans clé de `?questions=true` chez Greenhouse est l'inconnue la plus lourde.** Si elle existe, la phase 2 obtient gratuitement le schéma de formulaire du plus gros fournisseur et la qualité de l'extraction change d'ordre de grandeur. Si elle n'existe pas, tout Greenhouse passe par l'analyse HTML d'une page en iframe, ce qui est nettement plus fragile.
- **Le bouclier Cloudflare sur les formulaires hébergés est l'inconnue la plus décisive.** Il détermine si `auto_assisted` est atteignable sur la majorité du volume ou sur une marge. Je n'ai pas pu le vérifier faute d'accès réseau sortant.
- **La colonne `job.apply_url`.** Si elle n'existe pas ou porte un autre nom, l'amorce de la cascade change. À confirmer contre le vrai code avant d'écrire la première ligne.
- **L'échéance Recruitee du 10 février 2027.** Faut-il investir dans un fournisseur dont une route se referme dans quinze mois ? Mon avis : le traiter comme `ats_hosted_form` comme les autres et ne rien construire sur le `POST` sans jeton.
- **Workday, SAP SuccessFactors et Taleo sont absents des deux tiers de la phase 1**, alors qu'ils couvrent une part importante des grands employeurs belges. Les classer `account_portal` et `manual_only` est correct techniquement, mais cela signifie que le pipeline ignore de fait une tranche du marché. Arbitrage à poser : Dan accepte-t-il de candidater à la main sur ces employeurs, ou les retire-t-il de son périmètre ?
- **La lecture des conditions d'utilisation reste à faire, par Dan, fournisseur par fournisseur.** Le fichier `ats_policy.toml` est prêt à la recevoir ; il démarre à `unknown` partout, et `unknown` n'est pas bloquant par défaut. C'est un choix à valider explicitement : si Dan préfère que `unknown` bloque, c'est une ligne à changer dans la table de décision.
- **Le plafond de 20 offres par jour est-il compatible avec `auto_assisted` ?** Vingt confirmations manuelles par jour, c'est un coût réel. Arbitrage à poser avant la phase 4.
- **Les questions à réponse libre marquées `question_hard_blocker`** (niveau de néerlandais exigé, habilitation, années d'expérience) peuvent disqualifier une candidature indépendamment de la qualité du dossier. Faut-il que la phase 2 les traite comme un simple avertissement, ou qu'elles fassent basculer l'offre en `manual_only` pour que Dan décide lui-même ? Je recommande l'avertissement, parce qu'un seuil d'expérience est souvent négociable et que le filtre ne doit pas supprimer — mais c'est son choix.
- **La détection de la dérive de formulaire via `form_hash` suppose une reconstruction périodique.** Sans elle, un plan vieux de trois semaines peut décrire un formulaire qui a changé. Le seuil de péremption est un paramètre à fixer ; une semaine semble raisonnable pour une offre active.
