# jobpipe — Audit de cohérence des plans des phases 2, 3 et 4

Document d'audit. Je ne produis ici aucun plan et aucun code : je compare les trois plans entre eux et contre les contraintes posées.

Périmètre audité :
- `plan-phase2.md` — analyse de la page de candidature (414 lignes)
- `plan-phase3.md` — adaptation CV et lettre (984 lignes)
- `plan-phase4.md` — envoi, preuves, journal, notification (1 135 lignes)

---

## Verdict

**Pas prêt.** Onze anomalies bloquantes, dont dix de contrat de données ou de sémantique de garde-fou.

Les trois plans sont individuellement solides et honnêtes sur leurs limites, mais ils décrivent trois systèmes qui ne s'emboîtent pas : trois identifiants d'offre différents, trois énumérations de routes différentes, et des objets que deux phases consomment sans qu'aucune ne les produise. Deux défauts sont pires qu'un simple désaccord de nommage : la phase 4 peut atteindre l'envoi sans vérifier le portillon anti-fabrication de la phase 3, et le chemin `draft → approved` de la phase 3 n'est défini que par une action humaine, ce qui rend le mode autonome de la phase 4 mécaniquement inatteignable. Donné tel quel à Claude Code, cet ensemble produirait trois modules qui ne compilent pas ensemble.

---

## Anomalies bloquantes

### B1 — Trois identités d'offre incompatibles

| Plan | Table | Clé | Type |
|---|---|---|---|
| Phase 1 / 2 | `job` | `job.id` | INTEGER |
| Phase 3 | `offers` | `offer_id` | TEXT |
| Phase 4 | `job` (lu) + `application` | `canonical_key` | TEXT |

La phase 3 écrit littéralement `offer_id TEXT NOT NULL REFERENCES offers(offer_id)` dans `generation_runs`. Ni la table `offers` ni une clé d'offre textuelle n'existent dans l'architecture de la phase 1 telle que spécifiée. La phase 4 joint par `canonical_key` alors que le point d'entrée de lecture de la phase 2 est `get_apply_plan(job_id: int)`, qui n'accepte pas `canonical_key` et n'expose aucun index dessus hors de `apply_plan.canonical_key`.

Correction : une seule identité d'offre, `job.id` INTEGER, propagée partout ; `canonical_key` reste un attribut de déduplication, pas une clé de jointure inter-phases ; la phase 3 renonce à `offers`/`offer_id`.

### B2 — Trois énumérations de routes, et un alignement faussement affirmé

| Phase 2 (`ApplyRoute`, 8) | Phase 3 (`submission_channel`, 4) | Phase 4 (`ApplyRoute`, 6) |
|---|---|---|
| `ats_api_submit` | `form` | `ats_api` |
| `ats_hosted_form` | `email` | `hosted_form` |
| `employer_site_form` | `external` | `email` |
| `email_apply` | `unknown` | `account_portal` |
| `account_portal` | | `external` |
| `aggregator_redirect` | | `unknown` |
| `closed` | | |
| `unknown` | | |

La phase 4 affirme en section 3 que son énumération est « alignée sur le champ `apply_path` existant et sur le plan de la phase 2 ». Elle ne l'est sur ni l'un ni l'autre. Les écarts : suffixes divergents (`ats_api_submit` / `ats_api`, `email_apply` / `email`) ; effondrement 2→1 de `ats_hosted_form` + `employer_site_form` vers `hosted_form`, qui détruit la distinction que la phase 2 a construite exprès (hôte ATS connu contre site employeur, fragilité de sélecteurs très différente) ; `aggregator_redirect` et `closed` sans contrepartie en phase 4 ; `external` existant en phase 4 et **jamais produit** par la phase 2, qui le résout en `employer_site_form`, `aggregator_redirect` ou `unknown`.

Ce dernier point n'est pas cosmétique : toute l'estimation de volume de la phase 4 (section 11, puis la conclusion de la section 0) repose sur un segment « `external` / `unknown` : 35–50 % » construit sur les étiquettes non résolues de la phase 1. La phase 2 récupère l'URL, suit les redirections et re-résout la route, donc une large part de ce segment se reclasse en route de formulaire. Les deux tables de distribution ne peuvent pas être vraies en même temps.

Correction : une énumération unique, définie par la phase 2, importée telle quelle par les phases 3 et 4 ; la phase 4 refait sa table de la section 11 sur le vocabulaire de la phase 2.

### B3 — `ApplicationPlan` n'existe pas

Les phases 3 et 4 consomment toutes deux un objet `ApplicationPlan` doté d'un identifiant stable (`application_plan_id` en phase 3, `plan_id` en phase 4, tous deux TEXT). La phase 2 ne produit pas cet objet. Elle produit `ApplyPlanBundle(plan: ApplyPlan, fields, document_slots, obstacles)`, sans identifiant propre : la clé est `job_id`, et le versionnement se fait par `plan_revision` INTEGER.

Conséquence directe : `application.plan_id TEXT` (phase 4) et `generation_runs.application_plan_id TEXT` (phase 3) n'ont aucune valeur à recevoir, et `generation_runs` ne peut pas tracer quelle révision de plan a produit quels documents.

Correction : soit la phase 2 expose `plan_id = f"{job_id}:{plan_revision}"` comme identifiant stable, soit les phases 3 et 4 stockent le couple `(job_id, plan_revision)`. La seconde option est préférable, elle rend la dérive de plan détectable côté aval.

### B4 — `DocumentBundle` n'existe pas, et la clé d'idempotence de la phase 4 n'est pas calculable

La phase 4 consomme un `DocumentBundle` identifié par `document_bundle_id`, et stocke `application.document_bundle_id` et `application.document_bundle_hash`. La phase 3 livre un `ApplicationPackage` identifié par `package_id`, avec un `manifest.json` et un répertoire `outbox/`. Elle ne produit **aucun hash de lot** : seulement un `sha256` par fichier.

Or la clé d'idempotence de la phase 4 est `sha256(canonical_key | route | destination_identity | document_bundle_hash)`. Sans `document_bundle_hash`, la pierre angulaire de la protection contre le double envoi — un index unique en base, précisément choisi pour ne pas dépendre d'une vérification applicative qu'on peut oublier — n'est pas implémentable.

Écarts secondaires sur le même point de couture : la phase 4 exige un chemin **absolu** et un **type MIME** par fichier ; le manifeste de la phase 3 donne un chemin **relatif au répertoire du manifeste** et pas de type MIME.

Correction : la phase 3 ajoute `bundle_sha256` (hash des `sha256` triés, ou du manifeste canonicalisé) au manifeste et à `application_packages`, plus le type MIME par fichier ; les deux plans s'accordent sur `package_id` et abandonnent `document_bundle_id`.

### B5 — La transition `draft → approved` n'est définie que par une action humaine : le mode autonome est inatteignable

La phase 3 pose en règle n°1 de son contrat de sortie : « Ne soumettre que si `status == "approved"` ». Les valeurs de `application_packages.status` sont `draft | approved | held | superseded`, un paquet naît en `draft`, et le seul chemin vers `approved` décrit dans le document est `jobpipe review` (section 11.4), qui « exige un `approve` explicite écrivant `approved_by='human'` et `approved_at` ». Avec `review.required_tiers = [a]`, personne ne fait passer les paquets de tier B et C en `approved`.

Donc : en l'état, `--mode auto` de la phase 4 ne peut jamais envoyer quoi que ce soit, et `--mode approve` ne peut envoyer que du tier A. Ce n'est pas un désaccord de philosophie, c'est un état mort dans la machine.

Correction : définir explicitement qui écrit `approved` et quand. Trois valeurs distinctes sont nécessaires là où la phase 3 n'en a qu'une : « garde-fou propre, aucune revue requise » (prêt à l'envoi), « garde-fou propre, revue requise » (en attente), « garde-fou en échec » (refusé). Et `approved_by` doit admettre une valeur non humaine (`policy`) sinon la règle n°3 de la phase 3 bloque aussi.

### B6 — `required_skills` : personne ne les extrait

Le pré-score déterministe de la phase 3 (section 3.3), c'est-à-dire le cœur de la sélection du CV et le mode de référence `--no-llm-plan`, est défini comme « recouvrement entre ses `tags`/`evidence_refs` et **les compétences requises extraites par la phase 2** ». La phase 2 n'extrait pas les compétences requises : elle analyse le **formulaire de candidature**, pas le texte de l'offre. Son modèle de champ (`apply_field`) décrit des champs à remplir, jamais des exigences de poste.

Dépendent de cette donnée inexistante : le pré-score, `gap_skills`, `selection_plans.gap_json`, les métriques `keyword_coverage` et `unsupported_keyword_rate`, et la section 3.4 entière.

Correction : attribuer explicitement l'extraction des exigences du poste. Mon avis : à la phase 2, dans une table distincte (`offer_requirement`), par règles déterministes sur une liste fermée de compétences, avec l'offset dans le texte — pas au LLM, puisque la phase 3 en fait une entrée de scoring.

### B7 — La phase 4 peut atteindre l'envoi sans vérifier le portillon anti-fabrication de la phase 3

C'est le défaut le plus grave de l'ensemble, et c'est exactement le cas que l'audit devait chercher.

La phase 3 construit cinq couches de garde-fou et conclut par un L5 explicitement conçu comme une double négation : « La phase 4 refuse tout paquet dont le statut n'est pas `approved`. Le refus est du côté phase 4 autant que phase 3 : une double négation, pour qu'un bug de la phase 3 ne suffise pas à envoyer un document non vérifié. »

La phase 4 ne l'implémente pas. Trois trous distincts :

1. **La précondition de `docs_ready` ne lit pas le statut.** Elle dit : « un `DocumentBundle` de la phase 3 est attaché **et ses hashs sont vérifiés** ». Un paquet `held` dont les hashs sont bons franchit donc `docs_ready`. Le statut de la phase 3 n'apparaît nulle part dans la machine à états.
2. **`ungrounded_claim` est une rétention, pas un refus.** La classe 3 de la section 7.4 envoie en `awaiting_approval`, d'où une approbation en un clic ramène en `queued` puis en envoi. Un document que la phase 3 a jugé non traçable peut donc partir sur un clic depuis Telegram. La phase 3, elle, veut qu'il ne parte pas.
3. **L'indicateur dont dépend `ungrounded_claim` n'est pas défini.** La phase 4 l'écrit elle-même dans ses risques : « Si la phase 3 ne fournit pas cet indicateur, la rétention `ungrounded_claim` devient inopérante ». La phase 3 fournit `guard_summary.blocking`, `warnings`, `codes[]` — mais son juge L4 produit `unsupported` en `warning`, pas en `blocking`. Un document avec des phrases jugées non étayées a donc `blocking == 0` et franchit toute vérification de la phase 4 qui ne lit que ce compteur.

Correction : `docs_ready` exige `package.status == approved` **et** `guard_summary.blocking == 0` ; un paquet `held` va en `needs_manual`, jamais en `awaiting_approval` ; et le nom exact du champ portant la traçabilité est figé dans le contrat (je recommande `guard_summary.blocking` plus `guard_summary.l4_unsupported`, avec un seuil à décider).

### B8 — La phase 2 ouvre la porte que la phase 4 ferme au titre de l'article 550bis

La phase 4 pose une règle dure : « le pipeline n'appelle jamais un endpoint qui exige une clé d'employeur ». La phase 2 contredit cette règle à deux endroits :

- `ApplyRoute.ats_api_submit` est détectable sur « Fournisseur reconnu **et** crédentielle présente en configuration locale », et `feasibility = auto_full` est accordé sur « `route = 'ats_api_submit'` avec une crédentielle présente en configuration locale ».
- Pour Recruitee, la phase 2 prévoit `credential_kind = 'careers_site_token'`.

Une clé de Job Board API Greenhouse, une clé Lever, une permission `candidatesWrite` Ashby, un `X-Careers-Sites-Token` Recruitee : aucune n'est délivrée à un candidat. « Présente en configuration locale » ne peut donc désigner qu'une clé qui n'appartient pas à Dan. C'est littéralement l'hypothèse du §1er.

Correction : la condition de `ats_api_submit` devient « endpoint démontré ouvert sans crédentielle par `probe` » ; la branche `requires_credential = 1 → auto_full` disparaît ; `credential_kind` est conservé comme pure étiquette descriptive, jamais comme condition de faisabilité.

### B9 — La décision néerlandaise de la phase 3 est systématiquement retenue par la phase 4

La phase 3 décide, longuement argumenté et je pense à raison, de ne jamais générer de documents en néerlandais : offre néerlandophone, documents en anglais, `language_source = downgrade`. La phase 4 retient en `awaiting_approval` toute candidature où « la langue des documents ne correspond pas à celle de l'offre » (`language_mismatch`, classe 4 de la section 7.4).

Résultat : 100 % des offres néerlandophones, qui sont une part substantielle du marché bruxellois, tombent en approbation manuelle — non pas par décision, mais par collision de deux règles écrites séparément.

Correction : `language_mismatch` ne déclenche que si la langue des documents est hors de l'ensemble `{langue de l'offre, anglais}`, ou si `language_source != downgrade`. La bascule délibérée vers l'anglais est un comportement nominal, pas une anomalie.

### B10 — `auto_assisted` n'a pas d'état dans la phase 4

Le contrat de sortie de la phase 2 impose : « Sur `auto_assisted`, elle prépare et s'arrête avant le dernier clic, puis notifie. » La machine à états de la phase 4 n'a pas cet état. Son `awaiting_approval` a une sémantique différente : Dan approuve, **la machine clique**. Dans le modèle de la phase 2, Dan clique lui-même dans le navigateur, et c'est précisément ce qui fait de `auto_assisted` le livrable réaliste qu'elle défend.

Les deux sémantiques ont chacune leur mérite, mais elles ne sont pas la même et ne sont pas interchangeables : l'une laisse la soumission sous la main de Dan, l'autre la lui délègue après lecture. Aucune des huit classes de rétention de la phase 4 ne mentionne `feasibility == auto_assisted`.

Correction : trancher la sémantique (c'est aussi un arbitrage, repris plus bas), puis la nommer d'un seul côté. Si la phase 2 garde la sienne, la phase 4 a besoin d'un état `prepared_for_human` distinct de `awaiting_approval`.

### B11 — Le garde-fou de dérive de formulaire de la phase 2 n'a aucun consommateur

La phase 2 construit `form_hash` et `probe_fetched_at`, et impose : « Un plan dont `form_hash` diffère d'une reconstruction, ou dont `probe_fetched_at` dépasse le seuil de péremption, doit être reconstruit avant soumission. » La phase 4 ne mentionne jamais `form_hash`, n'a pas de code de raison de péremption, et ne vérifie pas la fraîcheur du plan avant de piloter un formulaire.

Conséquence concrète : un plan de trois semaines décrivant un formulaire modifié mène la phase 4 à remplir des sélecteurs périmés. Son assertion de préalable (« le template vérifie que tous ses sélecteurs obligatoires existent avant de remplir ») attrapera une partie des cas, mais pas un champ obligatoire **ajouté** par l'employeur : le template passe, le formulaire est rejeté côté serveur, et on retombe sur le mode d'échec silencieux `validation_rejected`.

Correction : la phase 4 vérifie `form_hash` et `probe_fetched_at` dans sa décision de garde-fou, avec un `hold_reason = plan_stale` et un rappel de `apply-plan build --refresh`.

---

## Anomalies à corriger avant construction

**A1 — `field_type` : 12 valeurs contre 8.** La phase 2 émet `text, textarea, email, phone, url, select, multiselect, boolean, date, number, file, consent`. La phase 3 déclare consommer `text, textarea, select, multiselect, boolean, number, date, file`. Les quatre manquantes (`email`, `phone`, `url`, `consent`) arriveront, et les modèles de la phase 3 sont en `extra="forbid"` avec des énumérations fermées : la validation échouera sur une offre parfaitement normale. Aligner sur les 12.

**A2 — Un seul modèle de champ contre deux listes.** La phase 2 a délibérément choisi une table unique `apply_field` avec le discriminant `field_class` (`standard | custom_question | compliance | consent | document`), en justifiant explicitement ce choix contre deux tables parallèles. La phase 3 consomme `required_fields[]` **et** `custom_questions[]` comme deux listes distinctes ; la phase 4 exige que les questions d'employeur soient « énumérées séparément ». Réconciliable par filtrage sur `field_class`, mais il faut l'écrire dans le contrat, sinon trois implémentations divergeront.

**A3 — Nommage des champs, un à un.** `question_key` / `field_key` · `question_text` / `label` · `char_limit` / `max_length` · `required` / `is_required` · `expected_files` / `apply_document_slot` · `role` / `slot` · `accepted_formats` / `accept_types`. Chaque écart est trivial seul et bloquant collectivement.

**A4 — `cv` contre `resume`.** La phase 2 nomme l'emplacement `resume` ; les phases 3 et 4 nomment le rôle `cv`. Trois tables, deux vocabulaires, le même objet.

**A5 — `accept_types` et `accepted_formats` n'ont pas la même sémantique.** La phase 2 produit « extensions et types MIME tels que déclarés par l'attribut `accept` », explicitement `NULL` quand l'attribut est absent, et justifie de ne rien deviner. La phase 3 attend « extensions en minuscules, sans point ». Un `accept=".pdf,application/pdf"` donne une liste hétérogène que le renderer de la phase 3 ne saura pas lire. Il faut une normalisation, et elle appartient à la phase 2.

**A6 — La stabilité de `question_key` est contredite par la phase 2 elle-même.** La phase 3 pose en hypothèse que `question_key` est stable entre deux analyses, et en déduit un cache de réponses. La phase 2 dérive `field_key` du `name`/`id` HTML et possède un détecteur `unstable_selector` pour les champs « sans `name`, sans `id` stable, ou avec un identifiant portant une empreinte de compilation ». La stabilité n'est donc pas une propriété, c'est un cas particulier. Le cache de réponses doit être invalidé par `form_hash`.

**A7 — `employer_facts[]` : personne ne les produit.** La phase 3 en fait la condition d'existence du bloc `fit` de la lettre, c'est-à-dire du seul bloc qui porte la spécificité. La phase 2 ne les produit pas, n'a pas de table pour les recevoir, et ne récupère que l'URL de candidature. Conséquence si rien n'est fait : le bloc `fit` est omis à 100 %, et `fit_block_omission_rate` vaut 1. Ce n'est pas une panne, mais une fonctionnalité documentée qui ne s'activera jamais.

**A8 — Les coordonnées du recruteur : même problème.** La phase 4 prévoit `employer_contact(name, email, phone, source)`, l'adressage `Madame <Nom>,` de la phase 3 en dépend, et la recherche `jobpipe recall` par téléphone est construite dessus. La phase 2 n'extrait que `email_recipient` pour la route courriel.

**A9 — `offer_body_text` et les offsets : personne ne les garantit.** La phase 3 exige « le texte intégral de l'offre, stable, avec des offsets de caractères utilisables pour référencer un `offer_evidence_span` », et impose `offer_evidence_grounding = 100 %`. La phase 2 ne transmet pas le texte de l'offre. Il est dans `job.description`, qui appartient à la phase 1 et que la phase 1 peut mettre à jour à la re-observation. Un offset enregistré pointe alors ailleurs, silencieusement. Il faut un `offer_text_sha256` figé, et personne ne l'a prévu.

**A10 — La langue de l'offre a trois propriétaires revendiqués et aucun confirmé.** La phase 2 suppose une colonne de langue en phase 1. La phase 3 fait sa propre détection avec `lingua-py`/`py3langid`. La phase 4 exige que la phase 2 la fournisse pour que `language_mismatch` soit évaluable. Un seul propriétaire, et ce devrait être la phase 1 si la colonne existe.

**A11 — `language_requirements[]` : personne ne les produit.** La phase 3 fonde sa règle néerlandaise (`hold_reason = nl_required`) sur « chaque entrée porte un code de langue et un niveau d'exigence (`required | preferred | mentioned`) ». La phase 2 n'a que `semantic_tag = 'language_level'` sur un champ de formulaire, ce qui est une autre information.

**A12 — `ApplicationPlan.status = partial` n'existe pas.** La phase 3 prévoit un traitement spécifique pour un plan partiel. La phase 2 n'a pas de colonne `status` : elle a `feasibility` et `feasibility_reason_code`.

**A13 — `answers[].source` est perdu dans le manifeste.** La phase 3 stocke `question_answers.source` (`declaration | computed | claims_bank | llm`) en base mais ne le reporte pas dans `manifest.json`. La phase 4 doit remplir `application_field.value_source` (`profile | phase3_generated | phase2_plan | manual`) et ne lit que le manifeste. Deux problèmes d'un coup : le champ manque, et les deux énumérations ne se correspondent pas.

**A14 — Visibilité des champs non transmise, règle honeypot inopérante.** La phase 4 pose : « refuser de remplir un champ invisible » et « ne jamais remplir un champ qui n'est pas explicitement listé dans le template ». La phase 2 extrait les champs de l'HTML statique sans marquer la visibilité (elle détecte des champs cachés de type `step`/`page` pour `multi_step`, mais ne l'expose pas comme attribut). Ajouter un `is_hidden` sur `apply_field`.

**A15 — Collision sur le répertoire `artifacts/`.** La phase 3 écrit `artifacts/<offer_id>/<run_id>/` ; la phase 4 écrit `artifacts/<sha256[:2]>/<sha256>`. Deux conventions de nommage dans le même arbre, dont l'une a des segments de 2 caractères hexadécimaux. À séparer : `documents/` pour la phase 3, `artifacts/` pour le magasin adressé par contenu de la phase 4, ou l'inverse.

**A16 — Collision de numérotation des migrations.** La phase 2 prévoit `migrations/00X_apply_plan.sql` et déclare s'appuyer sur l'exécuteur existant. Les phases 3 et 4 présentent des `CREATE TABLE` bruts, sans fichier de migration ni numéro. Écrites indépendamment, les trois voudront le même numéro. Il faut lire la table de version de la phase 1 et réserver les numéros avant d'écrire.

**A17 — Les trois stratégies d'isolation réseau en test sont mutuellement incompatibles.** La phase 2 remplace `socket.socket` par un objet qui lève, sans liste blanche, et affirme « Playwright n'est donc jamais lancé en test ». La phase 4 remplace `socket.socket.connect`, `create_connection` et `connect_ex` **avec une liste blanche loopback**, parce qu'elle pilote réellement Playwright en test sur des pages `file://`. Dans une suite unique, la fixture de la phase 2 casse les tests de la phase 4. La phase 3 n'a pas de fixture socket du tout, seulement des cassettes. Une seule fixture partagée, à portée de session, avec liste blanche loopback.

**A18 — `employer_key` est requis avant d'être calculable.** La phase 4 exige `application.employer_key TEXT NOT NULL` dès l'état `eligible`, et sa dérivation préférentielle est l'identité ATS `{vendor}:{board_token}`. Or `ats_vendor` et `ats_tenant` n'existent qu'après la phase 2, donc après `plan_ready`. Il faut soit accepter un `employer_key` dégradé (domaine, puis nom normalisé) à `eligible` puis le promouvoir, soit déplacer la dérivation après `plan_ready` — et dans les deux cas la fenêtre de verrou employeur doit tolérer une réécriture de clé.

**A19 — Deux schémas de priorisation non reliés.** La phase 3 a des tiers `a | b | c` « pilotés par le score d'offre de la phase 1 » et en fait une colonne NOT NULL partout. La phase 4 ignore les tiers et a une `watchlist_employer` éditée à la main. Les deux expriment la même idée — certaines candidatures méritent de l'attention humaine — par deux mécanismes différents dont aucun ne connaît l'autre. En plus, l'existence d'un score continu en phase 1 est une hypothèse non vérifiée : la phase 1 telle que spécifiée a des filtres à trois sorties, pas un score.

**A20 — Le run nocturne et la fenêtre d'envoi diurne sont incompatibles.** Les phases 2 et 3 concluent indépendamment que la chaîne se planifie en tâche nocturne (phase 2 : « cela se planifie en tâche nocturne, pas en exécution interactive » ; phase 3 : « c'est un travail de nuit »). La phase 4 n'autorise les envois qu'entre 08:00 et 19:00, du lundi au vendredi, heure de Bruxelles, avec un argument solide : un horodatage à 03:14 un dimanche est une signature. Les deux sont justes, mais ensemble ils impliquent que le pipeline **n'est pas** un run unique bout-en-bout : phases 1 à 3 la nuit, phase 4 en vidange de file le jour. Aucun plan ne décrit cet ordonnancement en deux temps ni qui le gouverne.

**A21 — Deux modèles LLM choisis séparément, un seul GPU.** La phase 2 prévoit un audit avec « un modèle 7B quantifié », la phase 3 recommande `qwen3:8b` en Q4_K_M avec un plafond de contexte à 4 096 tokens dûment justifié par la VRAM. Rien ne coordonne les deux. Si l'audit de la phase 2 et la génération de la phase 3 tournent sur des modèles différents dans la même chaîne, Ollama décharge et recharge entre les deux, et le coût n'apparaît dans aucun des deux budgets. Un seul registre de modèles, et de préférence un seul modèle.

**A22 — Désaccord sur Recruitee, et la phase 2 a raison.** La phase 4 recommande d'ouvrir `ats_api` sur Recruitee si `probe` confirme que l'endpoint accepte une requête non authentifiée. La phase 2 recommande de n'y rien construire. Les sources tranchent en faveur de la phase 2 : elle a lu la page d'authentification de `docs.recruitee.com`, qui exige l'en-tête `X-Careers-Sites-Token`, énonce que « Calls without the authorization header will return the `401 Unauthorized` error », et fixe l'obligation au 10 février 2027 ; la phase 4 n'a lu que la page de référence de l'endpoint, qui est silencieuse sur l'authentification, et qualifie elle-même sa confiance de « Moyenne [primaire, incomplet] » en ajoutant que « l'absence de mention peut refléter une page de référence incomplète ». Une documentation éditeur qui dit « ce jeton sera obligatoire, et sans lui c'est 401 » est une documentation qui dit que l'accès sans jeton n'est pas autorisé. Appeler l'endpoint quand même en espérant que ça passe encore est exactement la connaissance du défaut d'autorisation que vise le §1er de l'article 550bis. Un `probe` en lecture reste informatif ; la route reste fermée.

**A23 — Trois vocabulaires de sévérité.** Phase 2 : `blocking | degrading | informational`. Phase 3 : `blocking | warning | info`. Phase 4 : classes d'erreur et `hold_reason` sans échelle de sévérité. Harmoniser, sinon le rapport HTML mélangera trois échelles.

**A24 — La phase 3 ne décrit aucune isolation en écriture de son LLM.** Les phases 1, 2 et 4 ont chacune une vue en lecture seule et un test qui prouve qu'un `INSERT` lève. La phase 3 a un dispositif différent et en soi plus fort (le LLM ne produit que des identifiants validés contre le profil), mais elle ne dit jamais qu'aucune sortie de modèle n'atteint la base autrement que par un modèle Pydantic validé, et elle n'a pas le test correspondant. À énoncer, ne serait-ce que pour la symétrie des preuves.

---

## Anomalies mineures

- **Nommage des vues en lecture seule** : `v_apply_audit_input` (préfixe) en phase 2, `job_ro` / `application_ro` / `application_event_ro` (suffixe) en phase 4. Une convention.
- **Trois mécanismes de garde hors-ligne** : fixture socket (phase 2), `JOBPIPE_OFFLINE=1` plus constructeurs qui lèvent (phase 4), `CassetteClient` (phase 3). Les trois sont bons ; il en faut un jeu unique documenté une fois.
- **Deux formes de CLI d'évaluation** : `jobpipe eval --export-unlabeled --stage apply` (phase 2) contre `jobpipe eval phase3 --cases ...` (phase 3). Et la phase 4 n'a aucun harnais d'évaluation, seulement des tests.
- **Deux familles `Evidence*` de sens différent** : `EvidenceSource` et `Confidence` en phase 2 désignent la provenance d'une détection ; `EvidenceKind` en phase 4 désigne une preuve d'envoi. Même racine, concepts sans rapport.
- **Convention d'horodatage** : texte ISO 8601 UTC (phases 2 et 3) contre fenêtre horaire Europe/Brussels (phase 4). Stocker en UTC, convertir à l'affichage et à la décision horaire ; à écrire explicitement.
- **La phase 2 ne déclare pas `extra="forbid"` ni `frozen=True`** sur ses modèles Pydantic, alors que les phases 3 et 4 en font toutes deux un point d'architecture.
- **Qualification fine du 550bis par la phase 4, légèrement décalée.** J'ai vérifié le texte : le §1er punit de trois mois à un an et de 26 à 25 000 euros l'accès ou le maintien « sachant qu'il n'y est pas autorisé », sans intention frauduleuse requise ; avec intention frauduleuse, six mois à deux ans ; le §2 punit de six mois à deux ans celui qui « avec une intention frauduleuse ou dans le but de nuire, outrepasse son pouvoir d'accès ». La phase 4 rattache le contournement de captcha au §2 : il relève plutôt du §1er, puisqu'il n'y a pas de pouvoir d'accès préexistant à outrepasser. Inversement, le §2 est le bon cadre pour un portail où Dan a un compte — mais il exige une intention frauduleuse ou de nuire, que n'a pas une candidature. La **contrainte opérationnelle de la phase 4 est juste et doit être conservée telle quelle** ; seule la référence au paragraphe est à corriger, et le niveau de risque sur `account_portal` est en réalité un peu plus bas que la phase 4 ne le dit (le risque dominant y est la perte de compte, pas le pénal).
- **Aucune politique de rétention sur disque ni de sauvegarde.** La phase 3 conserve tous les runs, la phase 4 duplique les octets dans un magasin adressé par contenu, la phase 2 garde les corps de réponse (`apply_probe_log.body_path`) et des captures pleine page. Personne ne dit quand on purge, ni qui sauvegarde la base qui contient désormais tout le dossier de recherche d'emploi — que la phase 4 propose par ailleurs d'exporter comme preuve d'activité.

---

## Vérifications en attente contre le code réel de la phase 1

Liste unifiée et dédupliquée des hypothèses des trois plans portant sur la phase 1. Aucune n'est confirmée à ce jour. `$DB` désigne le fichier SQLite réel.

**Schéma et identité de l'offre**

1. `sqlite3 $DB ".schema job"` — la table s'appelle-t-elle `job` ? Quelle est la liste exacte des colonnes et de leurs types ? (Hypothèse commune aux trois plans ; la phase 3 suppose `offers`/`offer_id` TEXT.)
2. `sqlite3 $DB "select name, type, pk, \"notnull\" from pragma_table_info('job');"` — `id` est-il bien INTEGER PRIMARY KEY, utilisable comme clé étrangère ?
3. `sqlite3 $DB "select count(*), count(distinct canonical_key) from job;"` — `canonical_key` existe-t-il, et déduplique-t-il réellement ?
4. `sqlite3 $DB "select canonical_key, count(distinct source) c from job group by 1 having c > 1 limit 20;"` — la clé canonique fusionne-t-elle effectivement deux sources décrivant la même offre ? (Hypothèse explicite de la section 5.2 de la phase 4.)

**La mesure qui conditionne les phases 2 et 4**

5. `sqlite3 $DB "select apply_path, count(*) from job group by 1 order by 2 desc;"` — répartition réelle d'`apply_path`. Les deux plans la désignent indépendamment comme la première chose à mesurer avant d'écrire du code. Elle arbitre entre la table de distribution de la phase 2 et celle de la phase 4.
6. `sqlite3 $DB "select count(*) from job where apply_url is null;"` et `pragma_table_info` pour confirmer que la colonne existe sous ce nom. La phase 2 qualifie elle-même cette hypothèse de « la plus risquée du lot » : c'est l'amorce de toute sa cascade de détection.

**Filtres, score, langue**

7. `sqlite3 $DB "select <colonne_verdict>, count(*) from job group by 1;"` — le verdict de filtre porte-t-il exactement `pass`, `borderline`, `reject` ? Quel est le nom de la colonne ? Existe-t-il la colonne distincte indiquant si c'est le titre ou la description qui a déclenché le verdict ?
8. Existe-t-il un **score d'offre continu** en phase 1, ou seulement le verdict à trois valeurs ? Question au code : `grep -rn "score" <src>/filters/`. Les tiers `a | b | c` de la phase 3 en dépendent entièrement.
9. `sqlite3 $DB "select <colonne_langue>, count(*) from job group by 1;"` — la langue détectée est-elle stockée, sous quel nom, et avec quelles valeurs ? Y a-t-il un score de confiance ? Trois plans se la disputent.
10. Existe-t-il, en phase 1, une extraction des compétences ou exigences du poste ? Question au code : `grep -rni "skill\|requirement\|competence" <src>/`. Détermine si la lacune B6 est à combler en phase 2 ou en phase 1.

**Texte de l'annonce et re-découverte**

11. `sqlite3 $DB "select min(length(description)), avg(length(description)), max(length(description)) from job;"` — le texte intégral est-il conservé, ou tronqué ?
12. La phase 1 met-elle à jour `description` et `title` quand elle re-observe une offre modifiée ? Question au code : `grep -rn "UPDATE job\|on conflict" <src>/`. Si oui, les offsets `offer_evidence_span` de la phase 3 pourrissent silencieusement et il faut un `offer_text_sha256`.
13. `sqlite3 $DB "select first_seen_at, last_seen_at from job limit 3;"` — les horodatages sont-ils bien du texte ISO 8601 UTC, et sous quels noms exacts ?

**Infrastructure réutilisée par les trois phases**

14. `sqlite3 $DB "select * from <table_version_migration>;"` — quel est le nom de la table de version, et quel est le dernier numéro de migration appliqué ? Détermine les préfixes à réserver pour les phases 2, 3 et 4 (anomalie A16).
15. L'exécuteur de migrations accepte-t-il un nouveau fichier numéroté sans modification de son code ? Question au code : lire le module de migration.
16. Quel est le module et la signature exacte de l'utilitaire d'ouverture de connexion en **lecture seule** construit pour l'audit de la phase 1 ? Les trois phases prévoient de le réutiliser tel quel.
17. `sqlite3 $DB ".schema" | grep -i "create view"` — la vue en lecture seule existe-t-elle déjà, et sous quel nom (`job_ro` ? autre) ?
18. Quel objet de configuration unique porte le chemin de la base, et est-il importable depuis un autre sous-paquet ? Question au code : lire le module de configuration.
19. Le drapeau `--no-audit` et le répertoire de sortie du rapport HTML sont-ils définis au niveau de la CLI racine ou par phase ? Détermine si les conventions des phases 2, 3 et 4 héritent ou dupliquent.
20. Quel est le modèle d'enregistrement de résultat du sous-système `probe`, champ par champ, et est-il extensible sans le dupliquer ? La phase 2 prévoit `apply_probe_log` en extension, la phase 4 prévoit d'utiliser `probe` pour valider les fournisseurs et capturer les fixtures.
21. `sqlite3 $DB "select <colonne_verdict>, count(*) from job group by 1;"` confirme-t-il que les offres `reject` **restent** en base ? Les phases 2 et 4 en dépendent toutes deux (« les filtres étiquettent, ils ne suppriment jamais »).

**Environnement**

22. `ollama list` — quel modèle est réellement installé, avec quelle quantification et quelle empreinte ? Arbitre entre `qwen2.5:7b` (phase 2) et `qwen3:8b` (phase 3), et conditionne l'anomalie A21.
23. `soffice --version` — LibreOffice est-il présent ? Toute la chaîne de rendu et le contrôle de pagination de la phase 3 en dépendent, sans repli autorisé.
24. L'adresse d'envoi de Dan accepte-t-elle le sous-adressage `+` ? Son SPF/DKIM/DMARC est-il aligné pour un envoi via le relais prévu ? La phase 4 le signale elle-même comme à vérifier avant le premier envoi réel ; tout son suivi post-envoi repose dessus.

---

## Lacunes non prises en charge par aucune phase

1. **Orchestration bout-en-bout.** Aucune commande ne chaîne les quatre phases. Chaque plan a sa propre entrée (`apply-plan build`, `generate --offer`, le run de la phase 4) et son propre `run_id`. Il n'existe **aucune entité `run` partagée** : la phase 3 a `generation_runs.run_id`, la phase 4 a un `run_id` sur `application_event`, `submission_attempt` et `vendor_counter`, la phase 2 parle d'un « rapport de run » sans table. Impossible, en l'état, de répondre à « montre-moi tout ce qui s'est passé dans le run de mardi ».
2. **Ordonnancement en deux temps.** Conséquence de l'anomalie A20 : la chaîne nocturne et la fenêtre d'envoi diurne exigent deux planifications distinctes, et personne ne décrit laquelle déclenche laquelle, ni ce qui se passe si la file du matin contient des plans construits il y a trois nuits.
3. **Reprise après interruption au milieu de la chaîne.** La phase 3 a `generate --resume` par offre, la phase 4 a des baux et `submitted_unconfirmed`, la phase 2 a `--refresh`. Rien ne reprend une chaîne interrompue entre deux phases. Sur du matériel portable, pour une chaîne d'environ une heure, c'est le cas courant, pas le cas limite.
4. **Migration du schéma existant.** Trois lots de tables écrits indépendamment, sans numéros réservés, dont deux sans fichier de migration du tout.
5. **Configuration et secrets.** Aucun plan ne définit où vivent les identifiants SMTP et IMAP, le jeton du bot Telegram, l'hôte Ollama, ni comment ils sont chargés, ni quelles permissions de fichier, ni la règle « jamais dans git ». La phase 4 mentionne `send.enabled`, `JOBPIPE_SEND`, `JOBPIPE_OFFLINE` ; la phase 2 mentionne « crédentielle présente en configuration locale » ; la phase 3 ne mentionne rien. Avec une base contenant désormais des coordonnées de recruteurs, c'est une lacune à combler avant le premier envoi.
6. **Cycle de vie des artefacts de documents.** Voir les anomalies mineures : la phase 3 conserve tout, la phase 4 duplique tout, la phase 2 garde les corps de page et les captures. Aucune purge, aucun budget disque, aucune sauvegarde.
7. **Re-découverte d'une offre modifiée par l'employeur.** C'est la lacune la plus insidieuse parce que trois phases en couvrent chacune un morceau en croyant le sujet traité : la phase 2 a `form_hash` et `plan_revision` pour le **formulaire**, la phase 4 a `likely_repost` et `source_count` pour l'**offre republiée**, la phase 3 a `superseded` pour ses **documents**. Personne ne couvre « l'employeur a réécrit le texte de l'annonce alors que les documents étaient déjà générés ». Aucun hash du texte de l'offre n'existe nulle part, et les `offer_evidence_span` de la phase 3 sont des offsets de caractères dans ce texte.
8. **Cohérence du harnais d'évaluation.** Deux formes de CLI, deux formats de cas (`eval/phase3/cases.jsonl` contre un export annotable « au format annotable » de la phase 2), aucune évaluation pour la phase 4, et aucune métrique bout-en-bout du type « sur 100 offres découvertes, combien de candidatures effectivement parties et confirmées ». C'est pourtant le seul chiffre qui dira si le pipeline marche.
9. **Qui attribue la route finale.** La phase 2 refuse délibérément de réécrire `job.apply_path` — bonne décision, elle garde la phase 2 rejouable. Mais la phase 4 croit lire `apply_path` et la phase 3 attend un `submission_channel`. Il faut dire une fois pour toutes que la route faisant foi est `apply_plan.route` et que `job.apply_path` reste un indice de découverte.
10. **Fuseau et horloge.** La phase 4 injecte un `Clock` ; les phases 2 et 3 n'en ont pas, et écrivent des horodatages. Pas de convention commune, donc pas de test reproductible sur les fenêtres de péremption et de verrou.
11. **Journal des décisions humaines.** La phase 3 écrit `approved_by` / `approved_at` ; la phase 4 a `application_event` en ajout seul avec déclencheurs. Deux journaux, et les approbations de la phase 3 n'apparaissent pas dans le journal immuable de la phase 4, qui est pourtant celui conçu pour reconstituer l'histoire.

---

## Points de convergence indépendante

Les trois agents ne se sont pas parlé. Ces accords sont donc des conclusions robustes, et je les signale comme telles.

1. **Aucun ATS n'offre de point de terminaison de soumission utilisable par un candidat.** Deux revues de littérature séparées, sur les huit mêmes fournisseurs, par des chemins de sources différents, aboutissent à la même conclusion : la crédentielle est toujours employeur ou partenaire. Les phases 2 et 4 en tirent indépendamment la même conséquence d'architecture : la phase 4 n'est pas une phase « API ». C'est le fait le mieux établi de l'ensemble.
2. **Aucun contournement de protection, jamais.** Pas de résolution de CAPTCHA, pas d'empreinte falsifiée, pas de rotation de mandataires. Les trois plans l'écrivent, et deux d'entre eux avec le même raisonnement : le risque n'est pas technique, il est dirigé vers les employeurs auprès desquels Dan postule.
3. **Un jeton trouvé dans une page n'est pas une astuce d'intégration.** La phase 2 va jusqu'à prévoir un détecteur `credential_leak_seen` qui signale le fait sans jamais enregistrer la valeur ; la phase 4 y arrive par l'article 550bis. Deux chemins, même interdit.
4. **Le courriel est la route la plus automatisable de bout en bout**, et la plus rare. Convergence exacte, y compris sur la raison (ni CAPTCHA, ni sélecteur à maintenir, ni condition d'utilisation d'ATS à interpréter).
5. **`account_portal` ne doit pas être automatisé.** Phase 2 : « ne doit pas être combattu », classé `manual_only`. Phase 4 : catégorique, avec l'argument de l'irréversibilité de la perte de compte. Même verdict, arguments complémentaires.
6. **Le plafond de 20 par jour ne mordra jamais, et la cible réaliste est 5 à 8.** Trois arrivées indépendantes au même intervalle : phase 2 (« viser 5 à 8 offres très ciblées »), phase 3 (« 6 candidatures dont 2 vraiment travaillées »), phase 4 (« cible souple de 5 à 8 par jour »). Quand trois analyses séparées convergent sur un chiffre à partir de prémisses différentes — coût des confirmations, qualité des lettres, volume d'offres pertinentes — le chiffre mérite d'être tenu pour acquis.
7. **Une intervention humaine est nécessaire quelque part.** Les trois la recommandent, chacun l'ayant déduit de son propre domaine et chacun signalant franchement que cela contredit l'intention de départ. Aucun n'a cherché à l'enterrer. Ils divergent sur l'emplacement et le mécanisme, ce qui est l'arbitrage n°1 ci-dessous.
8. **Le LLM ne produit jamais de faits.** Phase 2 : il n'écrit que dans `apply_field_suggestion` et `apply_audit`, consultatives, et les phases aval ne les lisent pas. Phase 3 : on lui retire la capacité d'écrire le CV, il ne sélectionne que des identifiants dans un ensemble fermé. Phase 4 : il n'écrit que `classification` et `outcome_suggested`, jamais `outcome`. Trois formulations de la même règle, chacune avec son propre dispositif de preuve.
9. **Déterministe d'abord, LLM uniquement pour départager.** Phase 2 : marquage sémantique par table de données, pas par modèle, « parce que la phase 4 s'en servira pour décider ». Phase 3 : classifieur de questions déterministe d'abord, repli LLM pour départager, `unknown` par défaut. Même architecture, atteinte séparément.
10. **Isolation réseau en test au niveau socket, plus inversion de dépendances.** Les phases 2 et 4 décrivent presque le même dispositif, et toutes deux pour la même raison explicite : c'est ce qui rend crédible la promesse de testabilité hors-ligne, par opposition à une convention qu'on oublie. La phase 3 ajoute le client LLM enregistré/rejoué, qui est le morceau manquant des deux autres.
11. **`extra="forbid"` comme alarme de rupture de contrat.** Phases 3 et 4, mot pour mot le même raisonnement : un champ en trop doit faire échouer bruyamment, pas passer silencieusement. Ironiquement, c'est ce réglage qui transformera la plupart des anomalies ci-dessus en échecs immédiats — ce qui est précisément le comportement voulu.
12. **Le chiffre réel doit être mesuré avant de construire.** Phase 2 : « un run de `apply-plan build` sur une centaine d'offres tranche la question en une soirée, et ce chiffre devrait précéder tout investissement dans la phase 4 ». Phase 4 : « à faire avant d'écrire une ligne de la phase 4 : un comptage de `apply_path` sur le stock actuel de `job` ». Même instruction, formulée deux fois.
13. **La métrique qui compte est le faux positif sur la classe automatisable**, pas le rappel. Phase 2 : taux de faux `auto_full` à pousser à zéro, « au prix assumé d'un rappel médiocre ». Phase 3 : `fabrication_blocking` et `unsupported_keyword_rate` à zéro strict. Phase 4 : aucune transition vers `submitted` sans ligne de preuve. Le même raisonnement d'asymétrie — un faux négatif coûte trente secondes, un faux positif coûte une opportunité irréversible — appliqué trois fois.
14. **Le volume nuit sur un marché étroit.** Phases 3 et 4 développent séparément le même argument sur Bruxelles, avec des sources différentes, et aboutissent toutes deux à la même conclusion contre-intuitive : vingt candidatures non relues valent moins, en espérance, que cinq relues.
15. **PDF comme format par défaut.** Phase 2 par raisonnement (« le format le plus universellement accepté des quatre que Workable documente »), phase 3 par vérification sur source éditeur (Greenhouse, table de restrictions inter-ATS). Même conclusion, dont une vérifiée.

---

## Arbitrages à remonter à Dan

Fusionnés et dédupliqués depuis les trois plans. Je signale explicitement les cas où les agents recommandaient des choses différentes.

### 1. Où se place la validation humaine — et sous quelle forme

**Les trois agents divergent sur le mécanisme.** C'est l'arbitrage structurant, et c'est celui qui contredit l'intention de départ.

| Agent | Mécanisme proposé | Qui fait le geste final |
|---|---|---|
| Phase 2 | `auto_assisted` : le navigateur remplit le formulaire et s'arrête avant le dernier clic | **Dan clique dans le navigateur** |
| Phase 3 | `jobpipe review` entre phases 3 et 4, obligatoire sur tier A, `review.required_tiers` configurable | Dan approuve un paquet de documents, la machine envoie |
| Phase 4 | `--mode approve` par défaut, boutons Telegram ; ou `--mode auto` avec huit classes de rétention | Dan approuve depuis son téléphone, la machine envoie |

Options :
- **(a)** Tout autonome : `review.required_tiers = []`, `--mode auto`, les huit classes de rétention de la phase 4 restent le seul filet.
- **(b)** Autonome avec rétention ciblée : mode auto plus approbation obligatoire sur la `watchlist` et sur toute question libre. C'est le compromis que la phase 4 désigne elle-même comme probablement le meilleur.
- **(c)** File d'approbation pour tout, en un clic.
- **(d)** Modèle `auto_assisted` de la phase 2 : Dan fait le dernier clic lui-même.

*Recommandation :* **(b)**. L'argument décisif est arithmétique et vient de la phase 4 : le volume réellement automatisable étant de deux à six candidatures par semaine, l'autonomie totale fait gagner deux à trois minutes par semaine. Dans l'autre sens, **(c)** est une charge mentale permanente pour un gain marginal face à **(b)**, et **(d)** demande à Dan d'être devant sa machine, ce qui annule l'intérêt d'un run planifié.

*Ce que la décision débloque :* la sémantique de `auto_assisted` (anomalie B10), la transition `draft → approved` (B5), la définition de `awaiting_approval`, le choix du canal de notification ci-dessous, et le dimensionnement de la phase 3 (si la revue est obligatoire sur tier A, le budget de calcul se concentre là). **Rien de cohérent ne peut être écrit en phase 4 avant cette décision.**

### 2. Cible quotidienne sous le plafond

Options : utiliser les 20 ; cible souple de 5 à 8.
*Recommandation :* 5 à 8. Les trois plans y arrivent indépendamment (point de convergence 6). Au-delà, on candidate aux offres étiquetées `borderline`, c'est-à-dire à celles pour lesquelles le filtre avait un doute.
*Débloque :* la table `send_budget`, le budget de calcul de la phase 3, et la table de distribution de la phase 4.

### 3. Génération en néerlandais

Options : interdiction par défaut avec bascule vers l'anglais ; mesurer d'abord la qualité réelle via `--allow-nl-generation`.
*Recommandation :* confirmer l'interdiction. L'argument de la phase 3 est le bon : le coût d'une lettre néerlandaise générée n'est pas « lettre moyenne », c'est « candidat qui a menti sur son niveau », et une source officielle recommande l'anglais en cas de doute.
*Débloque :* l'anomalie B9 — la règle `language_mismatch` de la phase 4 ne peut pas être écrite avant de savoir si l'anglais sur offre néerlandophone est nominal ou exceptionnel. Débloque aussi le périmètre réel du pipeline, puisque cette décision en retire une part des offres bruxelloises.

### 4. Les grands employeurs sous portail à compte

Les phases 2 et 4 s'accordent sur « ne pas automatiser », mais la phase 2 pose une question que la phase 4 ne pose pas : Workday, SAP SuccessFactors et Taleo sont absents des deux tiers de sources de la phase 1, alors qu'ils couvrent une part importante des grands employeurs belges.
Options : Dan candidate à la main sur ces employeurs, dossier préparé par le pipeline ; ou il les retire de son périmètre ; ou on ajoute ces sources à la phase 1 pour au moins les voir.
*Recommandation :* dossier préparé, candidature manuelle. La phase 4 chiffre le gain à 80 % du travail pour 0 % du risque, et ces employeurs sont précisément ceux qu'on ne veut pas traiter au volume.
*Débloque :* la portée de `needs_manual` et la décision d'étendre ou non les sources de la phase 1.

### 5. Comportement par défaut de `submission_policy = unknown`

Options : `unknown` non bloquant (défaut proposé par la phase 2) ; `unknown` bloquant jusqu'à lecture des conditions d'utilisation, fournisseur par fournisseur.
*Recommandation :* non bloquant au départ, parce qu'un défaut bloquant rendrait le pipeline inerte avant que Dan n'ait lu huit jeux de conditions générales. Mais c'est une ligne dans la table de décision, et c'est à lui de la choisir sciemment, pas de la découvrir.
*Débloque :* `data/ats_policy.toml` et la table de décision de faisabilité.

### 6. Ouverture de la route `ats_api` sur Recruitee — les deux agents divergent

Phase 4 : l'activer si `probe` confirme un accès non authentifié. Phase 2 : n'y rien construire, échéance du 10 février 2027 inscrite dans la documentation de l'éditeur.
*Recommandation :* **suivre la phase 2**, pour les raisons de sources détaillées en A22, et parce qu'appeler sciemment un endpoint dont la documentation annonce un 401 sans jeton est exactement le cas du §1er de l'article 550bis. Un `probe` en lecture reste utile comme information ; la route reste fermée.
*Débloque :* le registre de fournisseurs de la phase 4 peut rester vide, et avec lui disparaît tout le code de la route `ats_api` — une simplification substantielle.

### 7. Que fait-on d'une question éliminatoire — les agents divergent

Phase 2 : simple avertissement, parce qu'un seuil d'expérience est souvent négociable et que le filtre ne doit pas supprimer. Phase 3 : `held` par défaut sur le tier A, `skip` sinon, et **toujours** `held` sur une question relative à l'absence de diplôme. Phase 4 : rétention de **toute** question libre, sans distinction.
*Recommandation :* la position de la phase 3, qui est la plus fine. L'avertissement de la phase 2 est trop faible sur la question du diplôme — c'est une déclaration engageante, pas une estimation ; la rétention universelle de la phase 4 est trop large et videra l'automatisation de son intérêt. Reste à décider `skip` ou `held` sur le seuil d'années d'expérience : je recommande `held`, parce qu'un `skip` supprime et que c'est contraire à la règle « les filtres étiquettent ».
*Débloque :* la catégorie `degree_requirement_hard_filter`, les classes de rétention de la phase 4, et la formulation à écrire une fois dans `claims_bank`.

### 8. Prétentions salariales

Options : fourchette stockée dans `declarations` et réponse automatique sur champ numérique fermé ; blocage systématique de toute offre qui l'exige.
*Recommandation :* fourchette dans `declarations` pour le champ numérique fermé, blocage sur le champ texte libre. C'est la position de la phase 3, et elle recouvre la classe `free_text_question` de la phase 4.
*Débloque :* `declarations.yaml` et la table de politique par catégorie de question.

### 9. Canal de notification

Options : fichier HTML seul ; fichier plus Telegram ; fichier plus courriel.
*Recommandation :* fichier comme source de vérité, Telegram pour la poussée. Les deux arguments de la phase 4 sont bons, et le second est décisif : la route `email` d'envoi utilise déjà SMTP, donc alerter par courriel couple le canal qui doit prévenir d'une panne à celui qui est en panne.
*Débloque :* lié à l'arbitrage n°1 — les boutons en ligne de Telegram sont ce qui rend l'approbation en un clic possible sans écrire d'interface web. Les deux décisions se prennent ensemble.

### 10. Relances automatiques

Options : automatiques à échéance ; brouillon préparé que Dan relit et envoie ; simple rappel dans le digest.
*Recommandation :* brouillon préparé. Une relance n'a de valeur que comme signal d'intérêt personnel, valeur détruite par le fait d'être générée.
*Débloque :* le périmètre de la phase 4, qui sinon s'étend indéfiniment.

### 11. Fenêtres du verrou employeur

Options : 1 candidature par `employer_key` tous les 14 jours et 3 sur 90 jours ; plus souple ; plus strict.
*Recommandation :* démarrer sur les valeurs proposées et les mesurer après un mois sur les vraies données. C'est le paramètre qui protège le plus sa réputation et celui qui frustrera le plus au quotidien.
*Débloque :* le verrou employeur et la table `employer_alias`.

### 12. Format de document par défaut

Options : `.pdf` ; `.docx` ; `.pdf` par défaut avec `.docx` sur ATS connu pour mieux parser à l'import.
*Recommandation :* `.pdf`. La troisième option demande une détection de fournisseur qui n'existe pas dans le contrat de la phase 2 ; la phase 2 produit bien `ats_vendor`, donc elle devient possible plus tard, sans bloquer maintenant.

### 13. Investissement humain initial dans `profile.yaml`

Ce n'est pas un arbitrage technique mais c'est un préalable chiffré : la phase 3 estime 60 à 90 puces pré-rédigées en français et en anglais, une à deux soirées, et précise qu'en dessous d'environ 60 la sélection manquera de matière et les CV se ressembleront d'une famille de poste à l'autre. S'y ajoute la formulation exacte du cursus ULB interrompu et de `claim.degree_context`, à écrire une fois, à la main.
*Débloque :* toute la phase 3. Sans cette matière, rien de ce qu'elle décrit ne fonctionne, et c'est le seul travail de l'ensemble que personne ne peut faire à sa place.

---

## Ordre de construction recommandé

Rien ne doit être écrit avant l'étape 0. Le principe de la phase 1, validée par étapes, est conservé : chaque étape produit un artefact vérifiable et un point d'arrêt.

### Étape 0 — Mesures et sondes, avant la première ligne de code

Les trois plans la demandent, et deux la nomment comme la première chose à faire. Rien ici n'est du développement.

**Contre la vraie base :** les vérifications 1 à 21 de la section précédente, en commençant par la 5 (répartition d'`apply_path`) et la 6 (existence de `job.apply_url`). Ces deux-là peuvent invalider une partie de la phase 2 et refaire les tables de distribution des phases 2 et 4.

**Contre l'environnement :** les vérifications 22 à 24 (`ollama list`, `soffice --version`, sous-adressage et alignement SPF/DKIM/DMARC).

**À sonder en lecture, par `probe` et dans cet ordre :**
1. `GET /v1/boards/{board_token}/jobs/{job_id}?questions=true` chez Greenhouse, sans clé. Si cela répond, la phase 2 obtient le schéma de formulaire du plus gros fournisseur et la qualité de son extraction change d'ordre de grandeur. Si cela ne répond pas, tout Greenhouse passe par l'analyse d'une page en iframe, nettement plus fragile. C'est la sonde la plus rentable de l'ensemble.
2. **Présence d'un bouclier Cloudflare ou Turnstile sur les formulaires hébergés de Greenhouse, Lever, Workable et Ashby.** Les deux plans la désignent indépendamment comme l'inconnue décisive, et elle l'est : elle détermine si la route `hosted_form`, c'est-à-dire la majorité du volume, existe ou s'effondre. Si Turnstile est généralisé, il ne faut écrire aucun template de formulaire, et le projet se recentre sur le courriel et la préparation de dossiers.
3. Disponibilité sans clé du `posting-api` d'Ashby.
4. Documentation primaire de Teamtailor, la seule que la phase 2 n'a pas pu atteindre.
5. Recruitee : une seule requête, pour information. Quel que soit le résultat, la route reste fermée (arbitrage n°6).

**Point de validation :** Dan dispose des chiffres réels d'`apply_path`, du verdict Cloudflare, et du schéma exact de la phase 1. Les arbitrages 1, 2, 3 et 6 sont tranchés. Les onze anomalies bloquantes sont corrigées **dans les plans**, pas dans le code.

### Étape 1 — Le contrat partagé, en premier et seul

Un module de contrat unique : énumérations partagées (une seule `ApplyRoute`, une seule échelle de sévérité, les rôles de document), identité d'offre unique, modèles Pydantic d'interface entre phases, en `extra="forbid"` et `frozen=True`, et les numéros de migration réservés. Plus la fixture d'isolation réseau partagée, avec liste blanche loopback, et le registre de configuration et de secrets.

Ce module résout à lui seul B1, B2, B3, B4, A1 à A5, A16, A17 et la lacune de configuration. Il doit être écrit **avant** toute phase, et aucune phase ne redéfinit ce qu'il contient.

**Point de validation :** les trois contrats se compilent ensemble et un test de bout en bout construit un objet factice de chaque type sans erreur de validation.

### Étape 2 — Phase 2, moitié déterministe

Dans l'ordre interne que la phase 2 propose elle-même : `apply-plan probe URL` d'abord, qui est l'outil de découverte et ne rien écrit en base ; puis `archive` pour constituer le corpus de fixtures ; puis le registre, la cascade, l'analyse de formulaire, la sémantique, les obstacles, la table de décision de faisabilité. Avec la correction de B8 : `ats_api_submit` conditionné à un endpoint démontré ouvert, jamais à une crédentielle locale. Plus les ajouts A7, A8, A9, A11, A14 et B6, qui sont les données que l'aval consomme et qui n'avaient pas de producteur.

**Point de validation :** un `apply-plan build` sur une centaine d'offres réelles déjà en base. C'est le chiffre que les phases 2 et 4 attendent toutes les deux. Si `auto_full` plus `auto_assisted` est sous les 10 %, l'ordre des priorités change : la phase 4 se réduit à la route courriel et à la préparation de dossiers, et la route `hosted_form` ne vaut peut-être pas sa dette de maintenance permanente.

### Étape 3 — Phase 3, dans l'ordre qu'elle propose, qui est le bon

Son plan d'implémentation est à conserver tel quel, parce que son argument est juste : le garde-fou et le renderer avant les appels LLM, sinon on juge la qualité à l'œil.

1. `profile.yaml` et `declarations.yaml` écrits par Dan, modèles et chargeur avec hash. **Point d'arrêt** : c'est son travail, pas celui de Claude Code, et rien ne peut avancer sans.
2. Garde-fou L0 à L3, corpus adverse et corpus propre. C'est l'oracle de tout le reste.
3. Renderer, round-trip, boucle de longueur.
4. Pré-score déterministe et `build_cv_doc()`. **Point de validation que la phase 3 signale elle-même, et il est bien placé :** à ce stade un CV adapté complet existe sans aucun appel LLM. Dan le lit. S'il est bon, le reste est du confort ; s'il ne l'est pas, le problème est dans `profile.yaml` et aucun modèle ne le réparera.
5. Routeur de langue et réponses déterministes.
6. Étage A sur cassettes, puis sur Ollama.
7. Étage B, la lettre.
8. Harnais d'évaluation, et la comparaison en trois colonnes dont `--no-llm-plan`.

**Point de validation :** l'évaluation dit si l'étage A bat le pré-score déterministe. Si non, il est retiré, et c'est une décision par les chiffres, pas par principe.

### Étape 4 — Phase 4, en trois temps, jamais d'un bloc

**4a. Policy et Orchestrator, sans aucun sender.** La couche de décision pure, la machine à états, le journal immuable, les baux, l'idempotence, le verrou employeur, l'interrupteur d'arrêt, le disjoncteur. Tout en `dry_run`, avec le `DryRunTransport` comme seul transport existant. Les treize tests de la section 12.4 passent ici, en particulier l'idempotence après crash injecté et la concurrence à deux orchestrateurs. Avec les corrections de B5, B7 et B11 : `docs_ready` lit le statut du paquet et le compteur de violations bloquantes, `held` va en `needs_manual`, et la fraîcheur du plan est vérifiée.

**Point de validation :** un run de simulation complet sur le jeu d'essai produit des `PlannedSubmission` lisibles pour les quatre routes, zéro octet émis, et Dan les relit. C'est le moment où il voit exactement ce que le pipeline s'apprête à envoyer, avant que l'envoi existe.

**4b. La route courriel seule.** C'est la plus solide, la plus simple à prouver, et celle sur laquelle les deux plans comptent le plus. Avec la passe de confirmation asynchrone, le sous-adressage, la fenêtre de grâce sans DSN. Premier envoi réel : **une seule candidature**, vers une adresse de contrôle d'abord, pour vérifier l'alignement SPF/DKIM/DMARC.

**Point de validation :** une candidature réelle part, est confirmée, et le dossier HTML produit permet de tout reconstituer. `jobpipe recall` répond en moins de deux secondes.

**4c. La route `hosted_form`, et seulement si l'étape 0 l'autorise.** Si la sonde Cloudflare a montré un bouclier généralisé, cette étape n'a pas lieu et le projet est meilleur pour autant. Sinon, un template à la fois, chacun avec sa classe de rétention `new_template` sur son premier envoi, et son assertion de confirmation.

### Étape 5 — Observabilité et suivi

Rapport HTML consolidé, dossiers par candidature, index FTS5, export de preuve d'activité, notification Telegram, relevé des relances à faire. Puis seulement la classification des messages entrants, qui est la seule partie LLM de la phase 4 et la moins urgente.

### Ce qui n'est explicitement pas dans cet ordre

L'orchestration bout-en-bout d'un seul `jobpipe run` ne doit pas être écrite avant l'étape 4b, parce qu'elle dépend de l'ordonnancement en deux temps (chaîne nocturne, vidange de file diurne) qui dépend lui-même de l'arbitrage n°1. Tant que ce point n'est pas tranché, chaque phase garde sa commande propre et Dan les enchaîne à la main. C'est moins élégant et c'est plus sûr.
