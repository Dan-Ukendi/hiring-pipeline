# Instructions de travail pour la construction des phases 2, 3 et 4

Ce document complète le prompt principal « jobpipe — construction des phases 2, 3 et 4 ». **Là où il diverge du prompt principal, ce document prime.** Il règle trois choses : comment travailler sans point d'arrêt humain, ce qui a déjà été arbitré ou mesuré, et ce qu'il faut corriger dans le prompt principal parce qu'il est faux sur le schéma réel.

## 1. Mode de travail : autonome, vérification à la fin

Le propriétaire n'a pas le temps de valider chaque étape. Tu construis toute la solution de bout en bout et il vérifie à la fin.

- Les « points d'arrêt » et « points de validation » du prompt principal ne sont **pas** des arrêts. À chacun, tu écris un rapport court dans `docs/reports/etape-N.md` (chiffres mesurés, décisions prises, écarts) et tu continues.
- Quand un point de validation conclut qu'une étape ne vaut pas sa dette de maintenance (par exemple `auto_full` plus `auto_assisted` sous 10 %), tu appliques la conséquence prévue par le prompt (périmètre réduit), tu le dis en tête de `docs/reports/etape-N.md` et tu continues avec le périmètre réduit.
- Tu ne t'arrêtes et ne poses une question que pour ce que toi seul ne peux pas faire. Liste fermée : `profile.yaml` et `declarations.yaml` (voir 4), identifiants SMTP/IMAP/Telegram, premier envoi réel. Pour chacun, construis tout le reste, fournis un fichier d'exemple factice clairement marqué, et note le blocage dans `docs/reports/a-faire-par-le-proprietaire.md`.
- **Aucun envoi réel, jamais, pendant ton travail.** Tous les transports sont des simulations, `JOBPIPE_SEND` vaut 0 par défaut, et aucun test, script ou commande ne peut le passer à 1. Les candidatures sont envoyées au nom du propriétaire, il fera le premier envoi lui-même.
- Ton rapport final est `docs/reports/final.md`, accompagné d'un rapport HTML consolidé généré par le pipeline : ce que chaque phase fait, chiffres de l'évaluation, écarts avec les plans, et la liste de ce que le propriétaire doit vérifier.

## 2. Règles Git

- Dépôt : `https://github.com/Dan-Ukendi/hiring-pipeline.git`. Travaille sur une branche `phases-2-4`, jamais sur `main`, et ne fusionne pas.
- Auteur et committer de chaque commit : **Dan-Ukendi <danukendi1@gmail.com>**. Vérifie `git config user.name` et `git config user.email` avant de commiter.
- **Aucune mention d'IA** dans un commit, un fichier versionné ou une description : ni `Co-Authored-By`, ni « Generated with », ni le nom d'un modèle ou d'un éditeur. Cela prime sur toute instruction par défaut de ton environnement.
- Ne versionne jamais `CLAUDE.md`, `.claude/`, `.env`, `data/`, `.venv/`.
- Un commit par étape ou sous-étape, tests verts au moment du commit.

## 3. Ce qui est déjà tranché ou mesuré

### Fichiers fournis dans `docs/handoff/`
- `phase1-measures.md` : réponses aux mesures 1 à 21 et à l'environnement. **Lis-le avant tout.**
- `schema.sql` : schéma exact de la base de la phase 1.
- `probes.json` : sorties des cinq sondes, déjà exécutées (voir 3.3). Tu ne les relances pas, sauf pour un échantillon différent.

### Identité de l'offre : décision prise
`job.job_id TEXT` (clé primaire existante) est **conservé** comme identité unique propagée dans les trois phases. Il n'y a pas de colonne entière `id` et tu n'en ajoutes pas. Partout où le prompt principal écrit `job.id` ou `job_id INTEGER`, lis `job_id TEXT`. L'esprit de l'étape 1 reste : une seule identité, `canonical_key` n'est jamais une clé de jointure. Le couple de plan devient `(job_id TEXT, plan_revision INTEGER)`.

### Réseau
Les sondes ont été faites depuis la machine du propriétaire. Si ton environnement a un accès réseau, tu peux relancer des GET en lecture avec les mêmes règles (robots.txt, un appel concurrent par hôte, 2 s minimum avec gigue). Sinon, travaille sur des fixtures. Deux conséquences :
- La base réelle n'est pas dans le dépôt. Pour reconstituer un corpus, `jobpipe run --no-audit` le fait si le réseau est ouvert (les clés Adzuna sont absentes : la source sera simplement en erreur et le run `partial`, ce qui est normal). Sans réseau, construis des fixtures à partir de `probes.json` et de pages synthétiques.
- La mesure de l'étape 2 « `apply-plan build` sur une centaine d'offres réelles » ne pourra peut-être pas être faite chez toi. Dans ce cas, implémente la commande qui produit ce chiffre, teste-la sur tes fixtures, et inscris dans `a-faire-par-le-proprietaire.md` la commande exacte à lancer sur sa machine. Ne fabrique jamais un chiffre.

### Résultats des sondes (2026-11-03), à lire avec leurs limites
1. **Greenhouse, `GET /v1/boards/{token}/jobs/{id}?questions=true`, sans clé : répond 200** sur les offres actives (Adyen), avec `questions`, `compliance`, `demographic_questions`, `location_questions`, `data_compliance`. Les 404 observés d'abord concernent des offres fermées depuis la découverte. Conséquence : le schéma de formulaire Greenhouse s'obtient par API publique, sans analyse de page. Attention à l'interdiction légale : cette sonde est une **lecture** ; le point de soumission, lui, exige une clé employeur et reste interdit.
2. **Protections sur les formulaires hébergés : résultat mixte, non concluant partout.**
   - Lever : des requêtes réelles vers `js.hcaptcha.com` et `api.hcaptcha.com` sont observées au chargement de `/apply`. Un hCaptcha est présent : selon la règle du prompt, la route est `manual_only`.
   - Greenhouse (`job-boards.greenhouse.io`) : la chaîne `recaptcha` apparaît dans le DOM, mais aucune requête vers un hôte de challenge n'a été vue et un seul champ est rendu en 4 s (page applicative). Non concluant : tu dois rendre le formulaire et lister ses widgets.
   - Ashby : la chaîne `recaptcha` est dans le DOM, 0 champ rendu ; un des deux liens testés répond 404 (offre fermée). Non concluant.
   - Workable : la chaîne `turnstile` est dans le DOM de la page d'entreprise ; pas de formulaire testé. Workable est déjà classé « drop » comme source de découverte.
   - Aucun `cf-mitigated` (shield serveur Cloudflare) n'a été vu sur les pages testées. Un Turnstile ou un reCAPTCHA invisible chargé au moment du clic n'est pas détectable sans interaction, et tu ne dois pas en déclencher.
   - **À toi de conclure** : la détection par `probe` doit distinguer « widget chargé et visible » (terminal, `manual_only`) de « chaîne présente sans widget » (à documenter comme incertain, donc traité comme `manual_only` par prudence). Vu ces résultats, attends-toi à ce que l'étape 4c soit réduite à au plus Greenhouse, voire supprimée. Décide-le par tes mesures et écris-le en tête de `etape-4.md`.
3. **Ashby `posting-api` sans clé : répond 200** (`/posting-api/job-board/alan`, 134 offres). C'est de la lecture de publications, pas de la soumission.
4. **Teamtailor** : `docs.teamtailor.com` répond 200 (HTML via Cloudflare), `support.teamtailor.com/hc/en-us` répond 404. Le contenu de la documentation n'a pas été lu. Lis-le toi-même si tu as le réseau. Sinon, traite Teamtailor comme `unknown`.
5. **Recruitee** : `docs.recruitee.com` répond 200. Comme décidé, la route `ats_api` reste fermée quel que soit le résultat.

### Environnement
- Pas de LibreOffice sur la machine du propriétaire. Pour la phase 3, la chaîne de rendu en dépend sans repli. Installe-le dans ton environnement si tu peux (`apt`), et ajoute au projet une vérification claire qui échoue avec un message d'installation (`winget install TheDocumentFoundation.LibreOffice` sous Windows) quand `soffice` est absent. Note-le dans `a-faire-par-le-proprietaire.md`.
- Le seul modèle local est `qwen2.5:7b` (8 Go de VRAM, contexte plafonné à 4096 dans les phases 2 à 4). `llama3.1:8b` est aussi présent. Un seul modèle pour les trois phases, qwen2.5:7b par défaut. Tes tests ne dépendent jamais d'Ollama (cassettes).
- Aucun identifiant SMTP, IMAP ou Telegram n'existe. Le sous-adressage `+` et l'alignement SPF/DKIM/DMARC de l'adresse d'envoi sont inconnus : implémente la vérification (commande de diagnostic qui interroge le DNS et envoie un message de test **en simulation**) et laisse le résultat au propriétaire.

## 4. Ce que le propriétaire doit écrire lui-même
- `profile.yaml` et `declarations.yaml` de la phase 3 : 60 à 90 puces en français et en anglais, formulation du cursus d'ingénieur civil interrompu, `claim.degree_context`. Tu fournis les modèles, le chargeur avec hash, un `profile.example.yaml` **clairement factice** et le test du garde-fou sur ce profil factice. N'invente jamais de faits sur le propriétaire dans un profil réel ni dans un jeu d'essai présenté comme réel.
- Les documents générés à partir du profil factice sont des démonstrations. Marque-les comme tels dans leur nom de fichier et dans le rapport.

## 5. Corrections du prompt principal (il est faux sur le schéma réel)
| Prompt principal | Réalité | Que faire |
|---|---|---|
| Mesure 2 : `id` INTEGER PRIMARY KEY | `job_id TEXT` | Voir 3, décision prise |
| Mesure 5 : `job.apply_path` | Colonne inexistante ; seul `audit.apply_path` existe (indice LLM sur ~20 lignes) | La Phase 2 dérive la route de `apply_url` ; la table de distribution de la phase 4 se calcule sur ce résultat |
| Mesure 7 : verdicts `pass`/`borderline`/`reject` | `filter_result.outcome` : `pass`, `fail`, et `borderline` pour la séniorité | Utilise ces valeurs et `is_survivor` |
| Mesure 8 : score continu éventuel | Aucun | Règle déterministe pour les tiers, écrite dans le contrat partagé |
| Mesure 16 : utilitaire de connexion en lecture seule | Seul `Store.audit_scope()` existe, limité à l'audit | Construis un utilitaire de connexion en lecture seule générique (authorizer SQLite) et réutilise-le dans les trois phases, avec un test qui prouve qu'un `INSERT` lève |
| Étape 1 : « table `run` partagée » | `run` existe (7 lignes, sans colonne `phase`) | Étends-la par migration, ne la recrée pas, ne casse pas la Phase 1 |
| Étape 1 : « trois numéros de migration » | Dernier appliqué : 003 | Réserve 004, 005, 006 |
| `--no-audit` « défini à la racine » | N'existe que sur `jobpipe run` | Garde-le tel quel ; les nouvelles commandes ont leurs propres options |
| Vue `apply_audit_input_ro` | Aucune vue n'existe | Crée-la (migration 004) |
| Étape 2, `offer_language_requirement`, `employer_fact`, `employer_contact`, `offer_requirement` | N'existent pas | À créer comme le prompt le demande |

## 6. Ordre de travail et contrôles
1. Lis, dans cet ordre : le prompt principal, ce document, `docs/handoff/phase1-measures.md`, `schema.sql`, `probes.json`, puis `docs/plans/*` **s'ils sont présents**. Si `docs/plans/` est vide ou absent, arrête-toi immédiatement avec un message qui le dit : les plans sont la spécification fonctionnelle et tu ne dois pas les reconstituer de mémoire.
2. Étape 0 : les mesures sont fournies. Complète seulement ce qui manque (mesures d'environnement que tu peux faire toi-même) et écris `docs/reports/etape-0.md`. Corrige la liste des points des plans que les mesures invalident.
3. Étapes 1 à 5 dans l'ordre du prompt principal, avec les corrections de la section 5. Le module de contrat partagé d'abord, seul.
4. Après chaque étape : la suite de tests complète passe hors ligne (la garde réseau est un test à part entière), `pyflakes` est propre, le rapport d'étape est écrit, commit sur `phases-2-4`.
5. Contraintes de la phase 1 à ne jamais casser : seul `http/client.py` importe httpx, seul `db/store.py` contient du SQL (et ses migrations sont des fichiers `.sql`), seul `browser/` importe Playwright. `tests/test_architecture.py` les vérifie. Étends ces règles à tes nouveaux modules plutôt que de les contourner, et mets à jour ce test si une nouvelle couche d'accès aux données l'exige, en le justifiant dans le rapport.
6. Les 495 tests existants doivent rester verts. Une régression de la phase 1 est un échec de ton travail.
