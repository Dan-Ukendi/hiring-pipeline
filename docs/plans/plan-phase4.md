# jobpipe — Plan d'architecture de la phase 4 : envoi, notification, journal

Document de conception. Aucun code source n'est produit ici. Les noms de tables, colonnes, champs,
modèles et énumérations sont en anglais et sont destinés à être reportés tels quels dans
l'implémentation.

---

## 0. Résumé de la position

Trois conclusions encadrent tout le reste du document, et elles sont désagréables.

**Première conclusion.** Aucun des grands ATS n'offre de point d'entrée de soumission utilisable par
un candidat. La vérification est faite, sources primaires à l'appui (section 2) : chez Greenhouse,
Lever, Ashby, SmartRecruiters et Personio, l'endpoint de création de candidature est protégé par une
clé ou un jeton que seul l'employeur détient. La seule exception plausible trouvée est l'API « Careers
Site » de Recruitee. La « route API » n'est donc pas une route générique, c'est un cas particulier
mono-fournisseur à confirmer par `probe`.

**Deuxième conclusion.** La part des offres réellement envoyables de bout en bout, sans aucune main
humaine et avec une confirmation positive, se situe à mon estimation entre 10 % et 20 % des offres
découvertes, et probablement vers le bas de cette fourchette une fois appliquées les conditions de
sécurité que je recommande (section 7). Sur un marché junior IA/data limité à la Région de
Bruxelles-Capitale, cela représente quelques candidatures par semaine, pas vingt par jour. Le plafond
de 20/jour ne mordra jamais. La contrainte réelle, c'est l'offre, pas le débit.

**Troisième conclusion.** Vu ce volume, l'envoi totalement autonome achète très peu de temps et
engage ce que Dan ne peut pas reconstruire : sa réputation sur un marché où les mêmes consultances
reviennent. Je recommande le mode file d'approbation en un clic comme défaut, avec le mode autonome
entièrement implémenté, testé, et disponible derrière un double verrou. Les deux modes partagent
100 % du chemin de code ; la seule différence est un état intermédiaire. Ce n'est pas une
sous-livraison, c'est un interrupteur. L'arbitrage reste à Dan, et il est posé en section 14.

---

## 1. Place de la phase 4 dans le pipeline

La phase 4 ne découvre rien, ne juge rien et ne rédige rien. Elle prend un plan de candidature
(phase 2) et un lot de documents (phase 3), et elle fait trois choses : décider si l'envoi peut
partir, le faire partir, et en garder une trace exploitable par un humain deux mois plus tard.

Elle n'écrit jamais dans `job`. La table `job` reste la propriété de la phase 1. La phase 4 lit
`job` et écrit dans ses propres tables, reliées par `canonical_key`. La couche LLM, si elle est
sollicitée en phase 4 (classification des réponses entrantes, résumé de run), reçoit la même vue en
lecture seule que celle mise en place en phase 1, et une vue équivalente `application_ro` sur les
tables de la phase 4. Un test le prouve (section 12).

Le module s'organise en quatre couches nettement séparées, parce que c'est la séparation qui rend le
test hors-ligne possible :

- **Policy** — décide. Pur, déterministe, sans effet de bord, sans réseau, sans horloge propre.
  Prend l'état du monde en entrée, rend un `GuardrailDecision`.
- **Orchestrator** — persiste les transitions, prend les baux, applique les temporisations, gère
  l'interrupteur d'arrêt et le disjoncteur.
- **Senders** — un par route. Derrière un protocole commun. C'est la seule couche qui touche
  l'extérieur, et elle le fait exclusivement à travers des transports injectés.
- **Recorder / Notifier** — journal d'audit, magasin d'artefacts adressé par contenu, rapport,
  notification.

---

## 2. État de l'art vérifié des routes de soumission (recherche)

Distinction explicite : **[primaire]** = documentation officielle du fournisseur lue directement ;
**[tiers]** = source secondaire ; **[non vérifié]** = je n'ai pas trouvé de source fiable, à mesurer
par `probe`.

### 2.1 Endpoints de soumission des ATS

| ATS | Endpoint de soumission | Authentification | Utilisable par Dan | Confiance |
|---|---|---|---|---|
| Greenhouse | `POST https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id}` | HTTP Basic sur TLS, nom d'utilisateur = clé d'API du job board, obtenue sur la page API Credentials de l'employeur | **Non** | Haute [primaire] |
| Lever | endpoint « Apply to a posting » documenté sous Postings ; chemin exact non confirmé | Basic avec clé d'API en nom d'utilisateur, ou OAuth ; portée `postings:write:admin` | **Non** | Haute sur l'auth, moyenne sur le chemin [primaire] |
| Ashby | `https://api.ashbyhq.com/applicationForm.submit` | clé d'API avec permission `candidatesWrite` | **Non** | Moyenne-haute [primaire] |
| SmartRecruiters | `POST /postings/:uuid/candidates` (Application API, section partenaires) | « All endpoints are protected » ; OAuth 2.0 avec portée `candidate_applications_manage` | **Non** | Moyenne-haute [primaire] |
| Personio | ressource `v1/recruiting/applications` en POST, déduite du slug de la référence ; la page descriptive est marquée dépréciée | non documentée sur la page lue ; l'API Personio fonctionne par jeton d'accès société | **Non** | Faible-moyenne [primaire partiel] |
| Recruitee | `POST https://{company}.recruitee.com/api/offers/{offer_slug}/candidates`, classé sous « Careers Site API », libellé « Apply for the job offer (create candidate) » | **aucune authentification mentionnée sur la page de référence** | **Peut-être — seule piste réelle** | Moyenne [primaire, incomplet] |
| Workable | aucune API de candidature côté candidat trouvée ; les candidatures passent par `apply.workable.com` | — | Non, sauf formulaire hébergé | [non vérifié] |
| Teamtailor | API employeur par clé `X-Api-Key` ; pas de route candidat documentée trouvée | — | Non, sauf formulaire hébergé | [non vérifié] |

Conséquence d'architecture directe : la route `ats_api` n'est pas une abstraction sur « les ATS ».
C'est un registre de fournisseurs, vide au départ, dans lequel on inscrit un fournisseur **après** que
`probe` ait démontré en lecture que l'endpoint existe et accepte une requête non authentifiée. Tant
que `probe` n'a pas validé Recruitee, la route `ats_api` reste désactivée et ne consomme rien.

Deux détails de débit utiles, tirés des docs primaires, à câbler dans le limiteur si ces routes
s'ouvrent un jour : SmartRecruiters limite à 8 requêtes concurrentes et répond 429 au-delà, et
recommande un timeout client d'au moins 128 secondes ; Lever annonce 10 req/s en régime établi,
rafales à 20 req/s, et recommande un repli exponentiel.

### 2.2 Ce que disent les conditions d'utilisation

Je n'ai trouvé, dans les documents primaires lus, aucune clause explicite interdisant la soumission
programmatique chez les éditeurs d'ATS eux-mêmes. Ce n'est pas un feu vert, c'est un silence, et il
s'explique : la relation contractuelle de l'ATS est avec l'employeur, pas avec le candidat. Les
conditions qui s'appliquent à Dan sont celles du **site de carrière de chaque employeur** et celles
des plateformes d'emploi — et là, ce n'est pas silencieux. Un job board consulté à titre d'exemple
représentatif énonce qu'aucune candidature ne peut être soumise par un agent, que le compte qui
candidate doit être celui de la personne et que les réponses doivent être les siennes, et que les
candidatures automatisées ou générées en masse violent ses conditions, avec pour conséquence que
« accounts that send them can be restricted or removed » [primaire, un seul board]. C'est la forme
type de ce qu'on trouve dans ce secteur, et il faut partir du principe que la clause existe partout
sous une variante ou une autre.

Les conditions d'utilisation d'Actiris que j'ai pu lire sont la version **employeurs** : elles ne
disent rien sur l'automatisation ni sur les obligations du chercheur d'emploi. Elles établissent en
revanche qu'un manquement entraîne « le blocage du compte de l'utilisateur », « immédiate et sans mise
en demeure préalable » [primaire, portée employeur]. Les conditions côté chercheur d'emploi n'ont pas
été localisées : **[non vérifié]**, à lire avant toute automatisation sur l'espace personnel Actiris.

### 2.3 Droit belge et RGPD

**RGPD.** L'article 2(2)(c) exclut du champ du règlement le traitement effectué « par une personne
physique dans le cadre d'une activité strictement personnelle ou domestique » [primaire, texte
vérifié]. Une recherche d'emploi menée pour soi-même par un particulier entre dans cette exemption de
façon assez nette. Dan n'est donc pas responsable de traitement pour son pipeline, il n'a pas de
registre à tenir, pas d'analyse d'impact, pas de base légale à documenter.

Deux nuances honnêtes. D'une part, Dan est le *sujet* de ses propres données, pas leur responsable, ce
qui règle la plus grosse partie du sujet par construction. D'autre part il va stocker des données
*tierces* : noms, adresses et téléphones de recruteurs dans `employer_contact`, et les courriels
entrants dans `inbound_message`. La Cour de justice lit l'exemption domestique étroitement dès que des
données sortent de la sphère privée. Conséquence pratique, et c'est tout : ces données restent locales,
ne sont ni publiées ni partagées ni réutilisées à d'autres fins, et sont supprimées sur demande. Je ne
gonfle pas le sujet au-delà de ça. Confiance : haute sur le texte, moyenne sur l'application.

**Droit pénal belge.** C'est ici que le risque est réel et il est mal connu. L'article 550bis §1er du
Code pénal punit « celui qui, sachant qu'il n'y est pas autorisé, accède à un système informatique ou
s'y maintient », de trois mois à un an d'emprisonnement et de 26 à 25 000 euros d'amende, et **aucune
intention frauduleuse n'est requise** : la connaissance du défaut d'autorisation suffit. Avec intention
frauduleuse, six mois à deux ans. Le §2 punit de six mois à deux ans celui qui, avec intention
frauduleuse ou dans le but de nuire, « outrepasse son pouvoir d'accès à un système informatique »
[primaire].

Traduction opérationnelle, et c'est une règle dure du plan : **le pipeline n'appelle jamais un endpoint
qui exige une clé d'employeur.** Appeler `boards-api.greenhouse.io` avec une clé qui n'est pas la
sienne, ou tenter de la deviner, c'est exactement le §1er. Ce n'est pas une zone grise de CGU, c'est du
pénal, sans besoin de prouver une intention frauduleuse. Le reste — piloter un formulaire public avec
Playwright, envoyer un courriel à une adresse publiée — n'est pas un accès non autorisé : le système
est ouvert au public et l'accès est autorisé. Le contournement de captcha, en revanche, rapproche
dangereusement du §2, parce qu'il s'agit de franchir délibérément un contrôle d'accès. D'où
l'interdiction en section 7.

**Ce que le droit belge n'interdit pas.** Candidater beaucoup. Utiliser un outil pour s'y aider. Un
courriel de candidature adressé à une adresse de recrutement publiée n'est pas une communication
commerciale non sollicitée : les règles d'opt-in du Code de droit économique visent la publicité, pas
une candidature. Confiance : moyenne-haute, non vérifiée sur source primaire.

---

## 3. Les cinq routes, et jusqu'où chacune est automatisable

Énumération `ApplyRoute`, alignée sur le champ `apply_path` existant et sur le plan de la phase 2.

### 3.1 `ats_api` — appel d'API de soumission

**Automatisable : oui, mais quasi inexistant.** Uniquement les fournisseurs inscrits au registre après
validation `probe`, c'est-à-dire Recruitee au mieux. Le flux : construire le corps multipart ou JSON
depuis le plan de la phase 2, attacher les artefacts de la phase 3, POST, lire l'identifiant de
candidature dans la réponse.

**Ce qui la fait échouer :** l'endpoint exige en réalité un jeton (403/401) — on désactive le
fournisseur et on bascule la candidature en `needs_manual`, on ne cherche pas de contournement ; le
schéma de champs du formulaire diffère de celui supposé (422 avec détail de validation) ; un champ
obligatoire inconnu de la phase 2 ; une limite de taille sur le CV ; un sous-domaine `{company}` mal
dérivé ; un changement silencieux de contrat d'API, qui se manifeste par un 200 sans identifiant.

**Degré :** automatisation complète avec confirmation forte quand ça marche, parce que la réponse
contient un identifiant vérifiable. C'est la meilleure route sur le plan de la preuve et la plus rare
en volume.

### 3.2 `hosted_form` — formulaire hébergé piloté par Playwright

**Automatisable : techniquement oui, structurellement fragile.** C'est la route la plus volumineuse et
la plus coûteuse à maintenir. Un « template » par forme de formulaire (Greenhouse board, Lever, Ashby,
Workable, Recruitee careers, Teamtailor careers, formulaires maison), chacun étant une description
déclarative : sélecteurs des champs connus, stratégie d'upload de fichier, sélecteur du bouton de
soumission, et surtout **assertion de confirmation** attendue après soumission.

**Ce qui la fait échouer, par ordre de fréquence attendue :**

1. **Questions libres de l'employeur.** « Pourquoi nous ? », « prétentions salariales »,
   « disponibilité », « avez-vous le droit de travailler en Belgique ». C'est l'échec le plus fréquent
   et le plus dangereux, parce qu'il ne provoque pas une erreur technique : il provoque une réponse
   générée qu'un humain lira. Traitement : passage obligatoire en `awaiting_approval` (section 7).
2. **Captcha ou Turnstile.** Présence non vérifiée sur les boards Greenhouse et Workable en 2026
   **[non vérifié]** — à mesurer par `probe`, pas à supposer. Détection d'un iframe ou d'un script de
   challenge connu avant toute action. Si détecté : `captcha_blocked`, route désactivée pour ce
   fournisseur sur le run, candidature en `needs_manual`. **Aucun service de résolution de captcha
   n'est intégré, jamais.**
3. **Upload de fichier en deux temps.** Beaucoup d'ATS envoient le CV vers un stockage objet par URL
   présignée, puis soumettent une référence. Un `set_input_files` qui « réussit » alors que l'upload
   asynchone a échoué donne un dossier avec un CV de 0 octet et une réponse 200. Mitigation :
   assertion sur le nom et la taille du fichier affichés par le widget après upload, et comparaison au
   hash de l'artefact.
4. **Jetons anti-CSRF et sessions courtes.** Le formulaire doit être chargé puis soumis dans la même
   session, sans fenêtre trop longue.
5. **Champs conditionnels et questions EEO.** Les questions démographiques (origine, handicap, genre,
   statut vétéran) sont présentes sur beaucoup de boards américains. Règle : **toujours répondre
   « I don't wish to answer » / laisser vide**, jamais de valeur inventée, jamais de valeur déduite.
   C'est l'application directe de la règle « la couche LLM n'invente jamais de données ».
6. **Changement de structure du DOM.** Détecté par l'échec d'une assertion de préalable (le template
   vérifie que tous ses sélecteurs obligatoires existent *avant* de remplir quoi que ce soit).
   `structure_changed`, aucun envoi tenté, aucune demi-soumission.
7. **Détection de navigateur automatisé.** Fingerprinting, absence de mouvement de souris, cadence
   trop régulière. Traitement : cadence humaine imposée par le limiteur (section 7), et acceptation du
   fait que cette route peut être bloquée sans avertissement.

**Degré :** automatisation de l'exécution, oui ; automatisation de la *décision*, non, dès qu'il y a
une question libre. Confirmation moyenne : assertion DOM plus capture d'écran, ce qui est une preuve
pour Dan mais pas un identifiant opposable.

### 3.3 `email` — candidature par courriel

**Automatisable : oui, c'est la route la plus solide de bout en bout.** Un SMTP authentifié sur sa
propre boîte, un message avec objet construit depuis le plan de la phase 2, un corps court, le CV et
la lettre en pièces jointes PDF, un `Message-ID` conservé, et une adresse `Reply-To` sous-adressée par
candidature (section 10).

**Ce qui la fait échouer :** adresse de recrutement obsolète ou piégée (le rebond arrive en
asynchrone, parfois après des heures) ; filtrage anti-spam du destinataire, qui est un échec
**totalement silencieux** ; limites d'envoi du fournisseur de messagerie (une boîte grand public
coupe autour de quelques centaines de messages par jour, largement au-dessus du plafond de 20, donc
non contraignant ici) ; SPF/DKIM/DMARC mal alignés si l'envoi passe par un relais qui n'est pas celui
du domaine de l'adresse `From` — point de vigilance réel, un courriel non aligné part en indésirable
sans aucun signal ; taille totale des pièces jointes ; adresse mise en forme comme `jobs [at]` sur la
page, que la phase 2 aurait mal désobfusquée.

**Degré :** automatisation complète. Confirmation **nécessairement asynchrone** : un `250` SMTP ne
prouve que la remise au premier relais. La preuve se construit en deux temps, détaillée en section 6.

### 3.4 `account_portal` — portail exigeant un compte

**Automatisable : oui techniquement. Je recommande de ne pas l'automatiser.** Workday, SuccessFactors,
Taleo, les portails des grandes consultances, l'espace personnel Actiris.

**Pourquoi je refuse l'envoi autonome sur cette route, et c'est l'un des deux points où je suis
catégorique :** l'automatisation suppose d'injecter ses identifiants dans un navigateur piloté. Les
conditions de ces portails interdisent typiquement l'accès automatisé et l'usage d'un agent, et le
compte est la seule chose dont la perte est irréversible — sur Workday, un compte perdu chez un
employeur peut emporter l'historique de toutes ses candidatures chez lui. Sur Actiris, le blocage du
compte a des conséquences qui dépassent largement le pipeline. Et c'est le seul scénario où la
question du §2 de l'article 550bis peut être posée, parce qu'il y a franchissement d'un contrôle
d'accès dans des conditions que le gestionnaire interdit.

**Ce que la phase 4 fait sur cette route :** elle prépare tout — documents, réponses aux champs connus,
un dossier lisible — met la candidature en `needs_manual` avec une URL directe, et la liste dans le
rapport de run. Dan colle et clique. Le gain de temps est de l'ordre de 80 % du travail, pour 0 % du
risque.

### 3.5 `external` / `unknown` — non automatisable

Valeurs déjà présentes sur `apply_path` en phase 1. `external` signifie que la piste sort vers un site
que la phase 2 n'a pas su caractériser ; `unknown` qu'elle n'a rien su dire. Dans les deux cas :
`needs_manual`, dossier préparé, zéro tentative. Ces deux valeurs sont probablement la majorité du
flux, et c'est la raison principale du chiffre de la section 0.

---

## 4. Machine à états

États de `application.state`, énumération `ApplicationState` :

```
eligible ──► plan_ready ──► docs_ready ──► queued ──┬──► submitting ──┬──► submitted
                 │               │            │     │                 │
                 │               │            │     │                 ├──► submitted_unconfirmed
                 │               │            │     │                 │
                 │               │            │     │                 ├──► failed_retryable ──► queued
                 │               │            │     │                 │
                 │               │            │     │                 └──► failed_permanent
                 │               │            │     │
                 │               │            │     └──► awaiting_approval ──┬──► queued
                 │               │            │                              └──► cancelled
                 │               │            │
                 ├───────────────┴────────────┴──► needs_manual
                 │
                 └──► skipped_duplicate / cancelled
```

Sémantique, état par état :

- `eligible` — l'offre a passé les filtres de la phase 1 avec `pass` ou `borderline`, et aucune
  candidature n'existe déjà pour sa `canonical_key`. Les filtres étiquettent, donc `borderline`
  n'empêche pas l'éligibilité ; il déclenche en revanche une rétention (section 7).
- `plan_ready` — un `ApplicationPlan` de la phase 2 est attaché, avec un verdict de faisabilité
  exploitable.
- `docs_ready` — un `DocumentBundle` de la phase 3 est attaché **et ses hashs sont vérifiés**.
- `queued` — les garde-fous ont rendu `allow`. La candidature attend son tour dans la file du run.
- `awaiting_approval` — les garde-fous ont rendu `hold`, avec un `hold_reason`. Seule une action
  explicite de Dan en sort.
- `submitting` — un bail est pris, l'intention est persistée et validée, l'envoi est en cours. Aucun
  autre processus ne peut entrer dans cet état pour la même candidature.
- `submitted` — une preuve positive est enregistrée dans `submission_evidence`. **Invariant : aucune
  transition vers `submitted` sans au moins une ligne d'évidence.** Un test le vérifie.
- `submitted_unconfirmed` — l'envoi est probablement parti, la confirmation manque ou est ambiguë.
  C'est l'état le plus important du plan et le plus souvent oublié. **Pas de reprise automatique
  depuis cet état, jamais.** Il déclenche une notification immédiate.
- `failed_retryable` — erreur dont on sait qu'elle n'a pas atteint le serveur. Retour en `queued`
  après temporisation, dans la limite de `max_attempts`.
- `failed_permanent` — erreur de classe non reprenable, ou `max_attempts` atteint.
- `needs_manual` — route non automatisable, obstacle bloquant, ou décision de politique.
- `skipped_duplicate` — doublon d'offre ou refus du verrou employeur.
- `cancelled` — interrupteur d'arrêt, ou décision de Dan.

**Persistance des transitions.** Chaque transition écrit une ligne dans `application_event`, table en
ajout seul. Des déclencheurs SQLite `BEFORE UPDATE` et `BEFORE DELETE` sur cette table lèvent une
erreur : le journal n'est pas modifiable, y compris par le code de la phase 4. Le changement de
`application.state` et l'écriture de l'événement se font dans la même transaction, de sorte qu'un état
sans événement correspondant est impossible.

**Le point dur : `queued → submitting → submitted_unconfirmed`.** La séquence est obligatoirement
celle-ci, et dans cet ordre :

1. Transaction A : passer en `submitting`, poser un bail (`lease_owner`, `lease_expires_at`), insérer
   une ligne `submission_attempt` avec son `idempotency_key`. **Commit.**
2. Transaction B : passer en `submitted_unconfirmed`. **Commit.** Rien n'est encore parti.
3. Effectuer l'appel réseau.
4. Transaction C : selon le résultat, passer en `submitted` avec les preuves, ou en
   `failed_retryable` / `failed_permanent`, ou rester en `submitted_unconfirmed`.

L'ordre compte. Si le processus meurt entre 2 et 4 — coupure de courant, OOM, fermeture du portable —
la base dit `submitted_unconfirmed`, c'est-à-dire « peut-être parti ». C'est la seule affirmation
honnête que la base puisse faire dans ce cas, et elle protège contre le double envoi. L'inverse
(écrire le résultat seulement après l'appel) produit une base qui dit `queued` alors que la
candidature est partie, et le run suivant la renvoie. C'est exactement l'accident à éviter.

---

## 5. Idempotence et protection contre le doublon

Trois niveaux, du plus technique au plus humain. Le troisième est celui qui compte vraiment.

### 5.1 Niveau candidature — la clé d'idempotence

`idempotency_key = sha256(canonical_key + "|" + route + "|" + destination_identity + "|" + document_bundle_hash)`

où `destination_identity` est l'URL de soumission normalisée ou l'adresse de destination, et
`document_bundle_hash` le hash du lot de documents.

Trois propriétés voulues. Elle est **déterministe**, donc un run qui reprend après un crash recalcule
la même clé et se heurte à l'index unique sur `submission_attempt.idempotency_key` : le double envoi
est bloqué par la base, pas par une vérification applicative qu'on peut oublier. Elle **inclut le hash
des documents**, donc une vraie nouvelle candidature après refonte du CV produit une clé différente et
reste possible — c'est volontaire. Elle **inclut la route**, donc un repli courriel après échec d'un
formulaire est un envoi distinct et traçable, ce qui est le comportement souhaité à condition que le
verrou employeur l'autorise.

### 5.2 Niveau offre — la clé canonique inter-sources

Index unique partiel sur `application(canonical_key)` restreint aux états non terminaux-négatifs. La
même offre remontée par Actiris et par le board Greenhouse de l'employeur produit une seule
`canonical_key` en phase 1, donc une seule ligne `application`. La seconde source met simplement à jour
`application.source_count` et ajoute un événement. C'est la phase 1 qui fait le travail ici ; la phase 4
en dépend et doit le dire (section 13).

### 5.3 Niveau employeur — le verrou qui protège la réputation

C'est le niveau que la clé canonique ne couvre pas et c'est celui qui coûte cher. Un employeur publie
« Junior Data Engineer » et « Data Analyst Junior » la même semaine, deux offres, deux clés canoniques
légitimement distinctes. Deux candidatures partent le même jour, avec deux lettres générées par le même
modèle à partir du même CV. Le recruteur, lui, voit deux messages quasi identiques en 24 heures. C'est
précisément le signal « candidature automatisée » qu'il a appris à écarter.

**Normalisation de `employer_key`**, par ordre de préférence décroissante :

1. Identité ATS, forme `{vendor}:{board_token}` — par exemple `greenhouse:acmecorp`. C'est le signal le
   plus fiable, parce que c'est littéralement le compte ATS de l'employeur.
2. Domaine enregistrable de l'URL de candidature ou de l'adresse de destination, extrait via la Public
   Suffix List. `careers.acme.be` et `jobs.acme.be` convergent sur `acme.be`.
3. Nom normalisé : minuscules, accents retirés, formes juridiques supprimées (`sa`, `nv`, `sprl`,
   `bvba`, `srl`, `bv`, `asbl`, `vzw`, `cvba`), ponctuation retirée, espaces normalisés.

Table `employer_alias` pour les fusions manuelles. Elle est indispensable et ce n'est pas un détail de
confort : à Bruxelles, les consultances IT se présentent sous plusieurs marques, sous-traitent les
unes pour les autres, et une même offre peut remonter sous trois noms. Dan doit pouvoir écrire une
ligne et déclarer que deux `employer_key` sont le même interlocuteur.

**Fenêtres de verrouillage proposées :**

- au plus **1 candidature par `employer_key` tous les 14 jours** ;
- au plus **3 candidatures par `employer_key` sur 90 jours** ;
- une offre bloquée par ce verrou passe en `skipped_duplicate` avec `reason_code = employer_cooldown`,
  et **reste disponible** : elle réapparaît dans le rapport sous « éligibles différées », avec sa date
  de déblocage. Elle n'est pas perdue, conformément à la règle « les filtres étiquettent, ils ne
  suppriment jamais ».

**Détection de reposting.** Même `employer_key`, similarité de titre élevée (distance de Jaro-Winkler
ou trigrammes, seuil à régler), écart inférieur à 60 jours, et candidature déjà envoyée : c'est très
probablement la même offre republiée. `skipped_duplicate`, `reason_code = likely_repost`. Renvoyer une
candidature sur un repost signale au recruteur qu'aucun humain ne suit le dossier.

---

## 6. Confirmation d'envoi et capture de preuves

Principe directeur : **un code HTTP n'est pas une confirmation.** La confirmation est une assertion sur
le *contenu* de ce que le serveur a renvoyé, ou sur un signal postérieur et indépendant.

### 6.1 Signaux par route

| Route | Preuve forte | Preuve faible | Artefacts capturés |
|---|---|---|---|
| `ats_api` | 2xx **et** identifiant de candidature extrait du corps | 2xx sans identifiant | statut, en-têtes retenus, corps tronqué à 64 Kio, empreinte de la requête hors secrets |
| `hosted_form` | assertion de confirmation satisfaite (sélecteur ou texte attendu du template) **et** réponse 2xx du XHR de soumission interceptée | URL finale changée mais texte de confirmation absent | capture d'écran pleine page PNG, HTML final, corps de la réponse de soumission, URL finale, trace Playwright si activée |
| `email` | accusé de réception reçu et corrélé, **ou** absence de DSN après la fenêtre de grâce | `250` SMTP et `Message-ID` | `.eml` du message envoyé, transcript SMTP, `.eml` de l'accusé reçu |
| `account_portal` | la candidature apparaît dans la liste du portail | — | hors périmètre autonome |

### 6.2 Distinguer un succès d'un échec silencieux

Taxonomie des échecs silencieux à traiter explicitement, parce que chacun s'est déjà vu :

- **Le formulaire renvoyé à l'identique avec 200.** Validation échouée côté serveur. Détection :
  l'assertion de confirmation échoue, et le DOM contient encore le champ de soumission. Classe
  `validation_rejected`, non reprenable sans intervention.
- **La page de challenge renvoyée avec 200.** Détection par signature de challenge connue avant
  l'assertion de confirmation. Classe `captcha_blocked`.
- **Le champ honeypot rempli.** Un champ caché que la page attend vide. Si le template le remplit, la
  soumission est acceptée en apparence et jetée côté serveur. Mitigation : ne jamais remplir un champ
  qui n'est pas explicitement listé dans le template, et refuser de remplir un champ invisible.
- **Le CV à 0 octet.** Détection par assertion sur le nom et la taille affichés après upload.
- **Le courriel avalé par l'anti-spam.** Indétectable directement. C'est pourquoi la confirmation de
  la route courriel reste au mieux une preuve faible, et pourquoi je recommande que
  `submitted_unconfirmed` soit un état *normal* et fréquent sur cette route, pas une anomalie.

### 6.3 Passe de confirmation asynchrone

Une étape `confirm`, exécutable séparément du run d'envoi, idempotente, sans réseau en mode
simulation :

- relève IMAP **en lecture seule** d'une boîte ou d'un libellé dédié ;
- corrélation des messages entrants par jeton de sous-adressage (`application.reply_token`), puis par
  `In-Reply-To` / `References`, puis par domaine d'expéditeur ;
- fenêtre de grâce de 24 heures sans DSN : `submitted_unconfirmed → submitted` avec
  `EvidenceKind.no_bounce_window` ;
- DSN reçu : `submitted_unconfirmed → failed_permanent`, `reason_code = bounced`, et notification,
  parce qu'une adresse morte signifie qu'il faut candidater autrement.

---

## 7. Garde-fous

### 7.1 Mode simulation par défaut, et double verrou

`dry_run` vaut vrai par défaut, partout, à tous les niveaux : valeur par défaut du paramètre, colonne
`submission_attempt.dry_run` avec `DEFAULT 1`, et transport par défaut `DryRunTransport`. Un envoi réel
exige **trois** conditions simultanées :

1. le drapeau `--send` sur la ligne de commande ;
2. `send.enabled = true` dans le fichier de configuration ;
3. la variable d'environnement `JOBPIPE_SEND=1`.

Ce n'est pas de la paranoïa décorative. Les deux premiers se trouvent dans des fichiers qu'un
planificateur peut porter ; le troisième ne survit pas à un copier-coller de commande et doit être
posé consciemment. Trois verrous rendent l'envoi accidentel pendant un développement pratiquement
impossible, et le coût est une ligne de documentation.

En mode simulation, chaque candidature produit un `PlannedSubmission` sérialisé : méthode, URL, carte
complète des champs avec leur provenance, noms et hashs des fichiers, corps du courriel. C'est lisible,
c'est diffable d'un run à l'autre, et c'est la charge utile de la file d'approbation. Le mode
simulation n'est donc pas un mode dégradé, c'est le mode de travail normal.

### 7.2 Plafond et cadence

- **Plafond journalier : 20, comme plafond.** Table `send_budget`, une ligne par jour, décrémentée
  dans la même transaction que la prise de bail — donc deux processus concurrents ne peuvent pas le
  dépasser. Au vu de l'estimation de la section 0, ce plafond ne sera jamais atteint. Je recommande en
  plus une **cible souple de 5 à 8 par jour**, non pas pour économiser une ressource, mais parce que
  huit candidatures ciblées sur ce marché épuisent déjà les offres pertinentes d'une journée, et
  qu'au-delà on candidate à des offres qu'on a classées `borderline` pour de bonnes raisons.
- **Débit par domaine :** seau à jetons, au plus 1 requête toutes les 5 secondes et 10 par minute vers
  un même domaine enregistrable. Valeur à abaisser si un fournisseur publie une limite — Lever annonce
  10 req/s, SmartRecruiters 8 requêtes concurrentes ; on reste très en dessous.
- **Débit par employeur :** le verrou de la section 5.3.
- **Espacement inter-candidatures :** délai aléatoire de 90 à 420 secondes, loi uniforme, source
  aléatoire injectée pour que les tests soient déterministes. Vingt candidatures en quatre minutes est
  un signal ; vingt sur une après-midi n'en est pas un.
- **Fenêtre horaire :** envois autorisés entre 08:00 et 19:00, heure de Bruxelles, du lundi au
  vendredi. Un horodatage de candidature à 03:14 un dimanche est une signature, et un recruteur la
  remarque. Hors fenêtre, les candidatures restent en `queued` jusqu'au prochain créneau.

### 7.3 Interrupteur d'arrêt et disjoncteur

**Arrêt global, trois mécanismes, chacun suffisant :**

- un fichier sentinelle `STOP` dans le répertoire d'état, dont l'existence est vérifiée *avant chaque*
  prise de bail. Le toucher depuis un autre terminal arrête le run proprement sans tuer de processus.
  C'est le mécanisme le plus simple et donc le plus fiable ;
- un gestionnaire de `SIGTERM`/`SIGINT` qui termine ou abandonne proprement le bail en cours, écrit
  l'état, et sort ;
- expiration de bail : un bail non renouvelé au-delà de son `lease_expires_at` est repris par le run
  suivant, qui trouve la candidature en `submitted_unconfirmed` et ne la renvoie donc pas.

**Disjoncteur, déclenchement automatique :**

| Condition | Effet |
|---|---|
| 3 échecs consécutifs sur un même fournisseur | fournisseur désactivé pour le run |
| 5 échecs consécutifs toutes routes confondues | run arrêté |
| 1 `captcha_blocked` | route désactivée pour ce fournisseur, sur le run |
| 1 `auth_required` (401/403) | fournisseur désactivé **définitivement** jusqu'à décision humaine, et consigné : le pipeline vient de toucher un endpoint protégé, ce que la section 2.3 interdit |
| 1 `submitted_unconfirmed` | notification immédiate ; le run continue, les autres candidatures ne sont pas concernées |
| 2 `submitted_unconfirmed` dans le même run | run arrêté : symptôme d'un problème systémique, pas d'un incident |
| taux d'échec du run > 50 % après 4 tentatives | run arrêté |

### 7.4 Ce qui ne doit jamais partir sans validation humaine

Voici ma proposition, et je la défends classe par classe. Ces huit cas passent en
`awaiting_approval`, avec un `hold_reason` nommé, même en mode autonome.

1. **`free_text_question` — l'employeur pose une question ouverte.** Prétentions salariales,
   disponibilité, « pourquoi nous ». Défense : une réponse à la question salariale qui part sans
   relecture peut coûter plusieurs milliers d'euros sur un contrat, ou disqualifier d'emblée. Ce n'est
   pas un risque de réputation, c'est un risque financier direct, et il est asymétrique : relire prend
   quinze secondes, se tromper coûte un an de différentiel salarial.
2. **`declaration_required` — une déclaration factuelle est exigée.** Diplôme, équivalence, permis de
   conduire, autorisation de travail, habilitation. Défense : Dan a arrêté son cursus d'ingénieur civil
   en BA3. Un formulaire qui demande « diplôme obtenu » n'a pas de bonne réponse automatique, et une
   réponse fausse dans un dossier de candidature n'est pas une maladresse, c'est une fausse
   déclaration qui peut annuler un contrat plus tard. Un humain doit choisir la formulation.
3. **`ungrounded_claim` — la phase 3 a produit une affirmation non traçable.** Si le lot de documents
   contient une phrase que la phase 3 ne peut pas rattacher à une source du CV, l'envoi est retenu.
   Défense : c'est l'extension logique de la règle « la couche LLM n'invente jamais de données ». Cette
   règle est inutile si le pipeline envoie quand même ce que le modèle a inventé. Le point de contrôle
   doit être au moment de l'envoi, parce que c'est le seul moment irréversible.
4. **`language_mismatch` — la langue des documents ne correspond pas à celle de l'offre.** Défense :
   Bruxelles est trilingue et les offres mélangent FR, NL et EN, parfois dans la même annonce. Envoyer
   une lettre française sur une annonce néerlandophone est une erreur immédiatement visible, et dans
   le contexte bruxellois elle n'est pas neutre.
5. **`new_template` — premier contact avec une forme de formulaire jamais confirmée.** Défense : le
   premier envoi sur un template non éprouvé est celui qui a le plus de chances de produire un dossier
   incomplet. Une relecture une fois par fournisseur, puis plus jamais : le coût est amorti sur toutes
   les candidatures suivantes.
6. **`account_portal` — route à compte.** Défense : section 3.4, risque irréversible et unique point
   d'exposition pénale.
7. **`watchlist_employer` — employeur sur la liste de Dan.** Défense : il y a dix à vingt employeurs à
   Bruxelles dont une candidature mérite vingt minutes de sa part. L'automatisation doit les protéger
   de son propre volume, pas les traiter comme les autres. La liste est un fichier qu'il édite.
8. **`borderline_filter` — l'offre a été étiquetée `borderline` en phase 1.** Défense : l'étiquette
   `borderline` existe parce que le filtre a rendu un verdict incertain. Envoyer automatiquement sur un
   verdict incertain, c'est convertir une incertitude assumée en action irréversible. L'étiquette doit
   avoir une conséquence, sinon elle ne sert à rien.

Résiduel réellement autonome une fois ces huit classes retirées : route `ats_api` ou `email`,
employeur hors liste, offre `pass`, zéro question libre, zéro déclaration, langue cohérente, documents
entièrement tracés, template éprouvé. C'est un sous-ensemble étroit, et c'est le sous-ensemble sur
lequel l'automatisation totale ne coûte rien. C'est aussi ce qui fonde le chiffre de la section 0.

---

## 8. Journal d'audit

### 8.1 Ce qui est enregistré

Pour chaque candidature, et de façon à ce qu'un humain puisse tout reconstituer sans lire de code :

- **l'offre** : `canonical_key`, `job_id`, intitulé, employeur, URL, **et un instantané figé de la
  description** au moment de l'envoi. Point non négociable : une annonce est dépubliée en moyenne bien
  avant qu'un recruteur rappelle. Si la phase 1 ne conserve pas le texte intégral, la phase 4 le
  capture et le range dans le magasin d'artefacts ;
- **la décision** : verdict du filtre, verdict de faisabilité de la phase 2, `GuardrailDecision`
  complet avec les règles évaluées et leur résultat ;
- **les documents envoyés** : les **octets exacts**, adressés par contenu dans `artifact`. Ce point
  mérite d'être explicite, parce que c'est le piège classique : la phase 3 régénère des documents et
  écrase ses fichiers de sortie. Un chemin stocké en base pointera dans trois semaines vers un CV qui
  n'est pas celui qui a été envoyé. On stocke donc le hash et une copie figée sous
  `artifacts/<sha256[:2]>/<sha256>`, et le nom de fichier tel qu'envoyé ;
- **les valeurs soumises, champ par champ**, dans `application_field`, avec la provenance de chaque
  valeur (`profile`, `phase3_generated`, `phase2_plan`, `manual`). C'est ce qui permet de répondre à
  « qu'est-ce que j'ai répondu à leur question sur mes disponibilités ? » ;
- **la tentative** : route, URL cible, horodatages, nombre d'essais, classe d'erreur, drapeau
  `dry_run`, `run_id` ;
- **les preuves** : lignes `submission_evidence` et leurs artefacts ;
- **le contact** : nom, adresse, téléphone du recruteur si la phase 2 les a extraits ;
- **la suite** : messages entrants corrélés, classification, issue.

### 8.2 Le scénario « un recruteur rappelle trois semaines plus tard »

C'est une exigence fonctionnelle, pas une note de confort, et elle dicte deux choses que la seule base
SQLite ne donne pas.

**Un dossier lisible par candidature.** À l'envoi, génération d'un fichier
`dossiers/<YYYY-MM-DD>-<employer-slug>-<job-slug>/dossier.html`, autonome, qui contient sur une page :
l'employeur et le contact, l'intitulé et l'URL, la date d'envoi, le texte de l'annonce tel que capturé,
les documents envoyés en lien relatif à côté, le détail des champs soumis avec les réponses, et les
preuves. Le répertoire contient les PDF réellement envoyés. Dan ouvre un fichier, il a tout. Pas de
requête, pas de script, pas de dépendance à l'état du pipeline.

**Une recherche par ce que le recruteur dit au téléphone.** Un recruteur annonce une société et
rarement un intitulé exact. Il faut donc un index de recherche sur le nom d'employeur, les alias, le
domaine, l'adresse et le numéro de téléphone du contact, qui rende la liste des candidatures envoyées
à cet interlocuteur avec, pour chacune, un lien vers son dossier. Une table FTS5 sur un document
concaténé par candidature suffit, et une sous-commande `jobpipe recall "<texte>"` la sert. Conçu pour
être utilisable pendant que le téléphone sonne : une commande, une réponse en moins de deux secondes.

**Un export de preuve d'activité.** Une sortie CSV et PDF listant date, employeur, intitulé, canal,
issue. En Belgique, un chercheur d'emploi peut avoir à documenter ses démarches auprès d'une
institution ; disposer d'un relevé daté et complet, généré automatiquement, est utile et ne coûte rien
puisque toutes les données sont déjà là. À produire par `jobpipe export --job-search-log`.

---

## 9. Notification

### 9.1 Recommandation

**Deux canaux, deux rôles distincts. Rapport HTML sur disque comme source de vérité, Telegram comme
canal de poussée. Pas de courriel.**

### 9.2 Argumentaire

Le choix dépend d'une seule question : qu'est-ce qui change entre la phase 1 et la phase 4 ? En phase
1, Dan est devant sa machine pendant le run, et un fichier sur disque est le bon choix — il l'ouvre
quand le run finit. En phase 4, deux choses changent. L'action est irréversible, et le run peut être
planifié, donc partir sans qu'il regarde. Un canal purement passif devient insuffisant : si le
disjoncteur se déclenche sur un `submitted_unconfirmed` à 14h et qu'il lit le rapport à 22h, il a perdu
huit heures pendant lesquelles il aurait pu vérifier la boîte du recruteur.

**Pourquoi pas le courriel, et c'est l'argument décisif.** Sa boîte est le canal sur lequel les
recruteurs répondent. C'est la seule boîte qu'il doit lire avec attention pendant cette recherche.
Y injecter un message de pipeline par run dégrade exactement le signal dont il a besoin, et au bout de
trois semaines les notifications sont filtrées, par lui ou par son client de messagerie. Second
argument, technique et peut-être plus grave : la route `email` d'envoi utilise déjà SMTP. Faire passer
l'alerting par le même transport couple le canal qui doit prévenir d'une panne à celui qui est en
panne. Quand le SMTP tombe, on perd les candidatures *et* l'alerte. C'est une faute d'architecture, pas
une préférence.

**Pourquoi Telegram.** Aucune dépendance SMTP. Un jeton de bot local, aucun service tiers à
provisionner, une API HTTP simple. Immédiat et sur le téléphone, ce qui répond au cas « le run est
planifié et il n'est pas devant sa machine ». Et surtout, Telegram est le seul canal de cette liste qui
porte des **boutons d'action en ligne** : si Dan retient l'option file d'approbation, l'approbation en
un clic depuis son téléphone devient possible sans écrire d'interface web. C'est l'argument le plus
fort, et il mérite d'être pesé avant de choisir le mode d'envoi, parce que les deux décisions sont
liées.

**Pourquoi garder le fichier.** Le rapport HTML est durable, consultable hors ligne, versionnable,
recherchable, et il fonctionne quand le réseau est coupé. C'est la source de vérité ; Telegram n'est
qu'un pointeur vers elle. Et en mode hors-ligne ou en test, `FileNotifier` est le défaut, donc aucun
test ne dépend de Telegram.

### 9.3 Agrégation

**Agrégation par run pour le digest, notification immédiate pour une liste courte d'événements.**

Le digest de fin de run, un message : compteurs par état, une ligne par candidature
(`employeur — intitulé — état — route`), les candidatures en `needs_manual` avec leur URL, les
`awaiting_approval`, les relances arrivées à échéance, et le chemin du rapport HTML. Une notification
par candidature serait vingt messages pour une information qui tient en un écran, et qu'on cesse de
lire dès la deuxième journée.

**Exceptions, poussées immédiatement, parce que chacune demande une action dans l'heure :**
`submitted_unconfirmed` (il faut peut-être vérifier auprès de l'employeur), déclenchement du
disjoncteur, `auth_required` (le pipeline a touché un endpoint protégé, c'est à corriger tout de
suite), activation de l'interrupteur d'arrêt, et toute demande d'approbation si le mode file est
retenu.

Protection contre le martèlement : au plus un message immédiat par classe d'événement et par run, le
reste est agrégé dans le digest.

---

## 10. Suivi post-envoi

### 10.1 Dans le périmètre de la phase 4

**La détection et l'enregistrement de la première réaction de l'employeur. Rien au-delà.**

- Relève IMAP **en lecture seule** d'une boîte ou d'un libellé dédié. En lecture seule par choix : le
  pipeline ne doit pas pouvoir marquer comme lu, déplacer ou supprimer un message de recruteur.
- Corrélation par **jeton de sous-adressage** : `application.reply_token` donne une adresse
  `Reply-To` de la forme `<local>+jp-<token>@<domaine>`. C'est la corrélation la moins chère et la plus
  fiable qui existe, elle survit aux changements d'objet et aux transferts internes, et elle ne
  demande qu'un fournisseur acceptant le `+`. Replis successifs : `In-Reply-To`/`References`, puis
  domaine d'expéditeur, puis rien.
- Classification en `acknowledged`, `rejected`, `interview_request`, `needs_info`, `unknown`. Si un LLM
  est utilisé ici, il écrit **uniquement** dans `inbound_message.classification` et
  `application.outcome_suggested`, jamais dans `application.outcome`, qui est factuel et validé par
  Dan. La règle de la phase 1 s'applique à l'identique.
- **Aucune réponse automatique, jamais.** Un `interview_request` déclenche une notification immédiate
  et s'arrête là.

### 10.2 Hors périmètre, et pourquoi

**Les relances automatiques.** Je recommande de ne pas les automatiser, et c'est un avis, pas une
contrainte technique. Une relance est un message adressé à un humain qui a déjà le dossier et qui
n'apporte aucune information nouvelle. Sa seule valeur est de signaler un intérêt personnel — valeur
qui est précisément détruite par le fait d'être générée. Une relance templatisée à J+10 chez trois
consultances qui se parlent produit l'effet exactement inverse de celui recherché. Ce que la phase 4
doit faire : calculer les échéances, les lister dans le digest sous « relances à faire », et
éventuellement préparer un brouillon que Dan relit et envoie. Le travail de mémoire est automatisé,
l'acte reste humain. Le coût pour lui : trois minutes par semaine.

**Le relevé de statut dans les portails ATS.** Demande des identifiants et une session authentifiée,
donc exactement la route que la section 3.4 écarte.

**Le suivi analytique du pipeline.** Taux de réponse par route, par type d'employeur, par version de
CV. C'est utile et c'est une phase 5. Les données sont là, l'analyse n'est pas un envoi.

---

## 11. Estimation chiffrée de la part automatisable

Méthode, pour que le chiffre soit discutable plutôt que décrété. Ordres de grandeur, et je le dis
clairement : ce sont des estimations raisonnées, pas des mesures. Seul un run réel de la phase 2 sur le
flux réel donnera les vrais chiffres, et **c'est la première chose à mesurer avant d'écrire la
phase 4**.

Sur 100 offres découvertes par les sources retenues en phase 1 :

| Segment | Part estimée | Automatisable de bout en bout |
|---|---|---|
| `external` / `unknown` — route non caractérisée | 35–50 | non |
| `account_portal` — portail à compte | 10–20 | non, par décision |
| `hosted_form` sans question libre, langue cohérente, template éprouvé | 8–15 | oui, confirmation moyenne |
| `hosted_form` avec question libre ou déclaration | 10–20 | non, approbation requise |
| `email` vers une adresse publiée et fiable | 8–15 | oui, confirmation faible puis asynchrone |
| `ats_api` sur un fournisseur ouvert (Recruitee au mieux) | 1–5 | oui, confirmation forte |
| captcha ou blocage détecté | 3–8 | non |

**Fourchette finale : 15 à 30 % envoyables techniquement sans intervention ; 10 à 20 % une fois
appliquées les huit classes de rétention de la section 7.4 ; et moins de 10 % avec une confirmation
positive forte.**

En valeur absolue, sur le marché junior IA/data de la Région de Bruxelles-Capitale, le flux d'offres
réellement pertinentes est de l'ordre de quelques dizaines par semaine au mieux. 10 à 20 % de cela,
c'est **deux à six candidatures entièrement autonomes par semaine**. Le plafond de 20 par jour n'a
aucune chance d'être approché. Je sais que ce chiffre rend la phase décevante par rapport à l'intention
de départ, et je préfère le poser maintenant que le découvrir après l'implémentation : le gain réel de
l'automatisation totale, par rapport à une file d'approbation, est de l'ordre de **deux à six clics par
semaine**.

---

## 12. Stratégie de test hors-ligne

### 12.1 Inversion de dépendances

Tout ce qui touche l'extérieur passe par un protocole injecté : `HttpTransport`, `BrowserDriver`,
`MailTransport`, `MailReader`, `Notifier`, `Clock`, `RandomSource`, `ArtifactStore`. Aucun module de
`senders` n'importe `httpx`, `smtplib` ou `playwright` directement. C'est cette discipline, et elle
seule, qui rend le test hors-ligne possible ; tout le reste de cette section n'est que vérification.

### 12.2 Le mécanisme qui garantit qu'un test ne peut pas sortir

Trois couches, de la plus grossière à la plus fine, et la première est celle qui compte :

1. **Blocage au niveau socket.** Une fixture `autouse` de portée session remplace
   `socket.socket.connect`, `socket.create_connection` et `socket.socket.connect_ex` par des fonctions
   qui lèvent une erreur dédiée `NetworkAccessForbiddenError`. C'est la garantie : n'importe quelle
   tentative de connexion réelle, par n'importe quel chemin, y compris une dépendance transitive
   inattendue, fait échouer le test bruyamment au lieu de partir discrètement. Une liste blanche
   contient uniquement le loopback, nécessaire au pilotage local du navigateur.
2. **Transports factices.** `httpx.MockTransport` avec des routes déclarées ; `page.route` de
   Playwright qui satisfait toutes les requêtes depuis des fichiers locaux ; `MailTransport` factice
   qui accumule les messages en mémoire. Playwright pilote un navigateur local via loopback et
   n'ouvre aucune connexion sortante dès lors que les pages sont servies en `file://` et que toutes
   les requêtes réseau sont interceptées — y compris les polices et les scripts tiers, dont
   l'interception non satisfaite doit faire échouer le test plutôt que passer silencieusement.
3. **Garde applicatif.** `JOBPIPE_OFFLINE=1` est posé par la configuration de test, et le
   constructeur de chaque transport réel lève si cette variable est présente. Ceinture et bretelles,
   mais le coût est de trois lignes.

### 12.3 Jeux d'essai

- `fixtures/forms/<vendor>/` — pages HTML réelles capturées **une fois** par `probe` en lecture seule
  (un GET sur une page publique, ce qui est l'usage normal du sous-système), expurgées de toute donnée
  personnelle et figées. Un fichier par forme de formulaire, plus la page de confirmation attendue,
  plus les variantes d'échec : validation échouée, challenge, champ obligatoire inattendu.
- `fixtures/responses/` — corps de réponse API, succès avec identifiant, 200 sans identifiant, 422
  avec détail, 401, 429, 503.
- `fixtures/mail/` — fichiers `.eml` : accusé de réception, DSN de rebond, refus, invitation à
  entretien, message hors sujet.
- `fixtures/plans/` et `fixtures/bundles/` — contrats des phases 2 et 3, dont des cas dégradés :
  champ obligatoire manquant, hash incohérent, question libre présente.

### 12.4 Ce que les tests doivent démontrer

Les tests qui comptent, et chacun correspond à un accident identifié dans ce document :

1. **Aucune sortie réseau.** La suite complète passe avec le blocage socket actif. C'est le test qui
   prouve tous les autres.
2. **Simulation complète.** Un run `dry_run` sur le jeu d'essai parcourt les quatre routes jusqu'au
   `PlannedSubmission` sérialisé, et le compare à un fichier de référence. Zéro octet émis.
3. **Pas de `submitted` sans preuve.** Test de propriété sur toutes les séquences d'événements : un
   état `submitted` sans ligne `submission_evidence` est impossible.
4. **Idempotence après crash.** Interruption injectée entre chaque paire d'étapes de la séquence de la
   section 4 ; reprise ; vérification qu'aucune candidature n'est envoyée deux fois et qu'aucune n'est
   perdue silencieusement.
5. **Concurrence.** Deux orchestrateurs sur la même base, même file : un seul prend le bail, l'index
   unique rejette l'autre, le plafond journalier n'est pas dépassé.
6. **Verrou employeur.** Liste d'offres fuzzée avec employeurs en alias, formes juridiques variées,
   sous-domaines multiples : les fenêtres de 14 et 90 jours tiennent, et rien n'est supprimé, tout est
   étiqueté.
7. **Classification des erreurs.** Pour chaque classe injectée, l'état final est légal et la décision
   de reprise est conforme. En particulier : un timeout après émission de la requête donne
   `submitted_unconfirmed` et **jamais** une reprise automatique.
8. **Échecs silencieux.** Le formulaire renvoyé à l'identique avec 200, la page de challenge en 200 et
   l'upload à 0 octet sont tous détectés comme des échecs.
9. **Les huit classes de rétention.** Chacune a son test : la candidature correspondante atteint
   `awaiting_approval` avec le bon `hold_reason`, même avec les trois verrous d'envoi posés.
10. **Interrupteur d'arrêt.** Un fichier `STOP` créé en cours de run arrête l'orchestrateur avant la
    prise de bail suivante, sans candidature à moitié envoyée.
11. **Isolation du LLM.** La session d'audit obtient une connexion sur les vues `job_ro` et
    `application_ro` ; toute écriture sur `application`, `submission_attempt` ou
    `submission_evidence` lève. Miroir exact du test de la phase 1.
12. **Immuabilité du journal.** `UPDATE` et `DELETE` sur `application_event` lèvent.
13. **Figement des artefacts.** Après envoi, régénérer le lot de la phase 3 avec un contenu différent
    ne change pas ce que le dossier d'audit restitue.

---

## 13. Contrat de données

### 13.1 Tables SQLite

**`application`** — une ligne par intention de candidature.
`id` INTEGER PK · `canonical_key` TEXT NOT NULL · `job_id` INTEGER · `employer_key` TEXT NOT NULL ·
`route` TEXT NOT NULL · `state` TEXT NOT NULL · `idempotency_key` TEXT NOT NULL ·
`plan_id` TEXT · `document_bundle_id` TEXT · `document_bundle_hash` TEXT ·
`hold_reason` TEXT · `approved_at` TEXT · `source_count` INTEGER NOT NULL DEFAULT 1 ·
`reply_token` TEXT · `first_queued_at` TEXT · `sent_at` TEXT · `confirmed_at` TEXT ·
`attempt_count` INTEGER NOT NULL DEFAULT 0 · `outcome` TEXT · `outcome_suggested` TEXT ·
`outcome_at` TEXT · `lease_owner` TEXT · `lease_expires_at` TEXT ·
`created_at` TEXT NOT NULL · `updated_at` TEXT NOT NULL.
Index unique partiel sur `canonical_key` pour les états non terminaux-négatifs. Index unique sur
`reply_token`. Index sur `(employer_key, sent_at)` et `(state, route)`.

**`application_event`** — ajout seul, déclencheurs interdisant `UPDATE` et `DELETE`.
`id` · `application_id` · `from_state` · `to_state` · `event_type` · `reason_code` · `detail_json` ·
`occurred_at` · `run_id`.

**`submission_attempt`**
`id` · `application_id` · `attempt_no` · `idempotency_key` TEXT NOT NULL UNIQUE · `route` ·
`target_url` · `request_fingerprint` · `started_at` · `finished_at` · `transport_status` ·
`error_class` · `is_ambiguous` INTEGER NOT NULL DEFAULT 0 · `dry_run` INTEGER NOT NULL DEFAULT 1 ·
`run_id`.

**`submission_evidence`**
`id` · `attempt_id` · `kind` · `artifact_sha256` · `summary` · `captured_at`.

**`artifact`** — index du magasin adressé par contenu, fichiers sous `artifacts/<sha256[:2]>/<sha256>`.
`sha256` TEXT PK · `kind` · `mime_type` · `byte_size` · `stored_path` · `created_at`.

**`application_document`**
`application_id` · `role` · `artifact_sha256` · `filename_sent` · `source_bundle_id`.

**`application_field`** — les valeurs réellement soumises.
`id` · `application_id` · `field_key` · `field_label` · `value_text` · `value_source` ·
`is_sensitive` INTEGER NOT NULL DEFAULT 0.

**`employer`**
`employer_key` TEXT PK · `display_name` · `vendor` · `registered_domain` · `is_watchlisted` ·
`notes` · `created_at`.

**`employer_alias`**
`alias_key` TEXT PK · `employer_key` TEXT NOT NULL · `merged_at` · `merged_by`.

**`employer_contact`**
`id` · `employer_key` · `name` · `email` · `phone` · `source` · `last_seen_at`.

**`send_budget`**
`day` TEXT PK · `cap` INTEGER NOT NULL · `sent_count` INTEGER NOT NULL DEFAULT 0 ·
`dry_run_count` INTEGER NOT NULL DEFAULT 0.

**`vendor_counter`** — pacing par fournisseur et par run.
`run_id` · `vendor` · `attempt_count` · `consecutive_failures` · `is_disabled` · `disabled_reason`.

**`inbound_message`**
`id` · `application_id` · `received_at` · `from_address` · `subject` · `classification` ·
`matched_by` · `artifact_sha256`.

**`application_search`** — table FTS5 pour `jobpipe recall`, alimentée par déclencheur : employeur,
alias, domaine, nom et téléphone du contact, intitulé, URL, extrait de l'annonce.

### 13.2 Vues en lecture seule

`job_ro` (existante, phase 1) et `application_ro`, `application_event_ro`, `inbound_message_ro`.
La session utilisée par la couche LLM n'ouvre que ces vues. Les colonnes sensibles de
`application_field` marquées `is_sensitive` sont exclues de `application_ro`.

### 13.3 Modèles Pydantic

Consommés depuis l'amont : `ApplicationPlan` (phase 2), `DocumentBundle` (phase 3).

Propres à la phase 4 : `SubmissionRequest`, `PlannedSubmission`, `SubmissionResult`, `Evidence`,
`GuardrailDecision`, `GuardrailRuleResult`, `EmployerIdentity`, `RunSummary`, `NotificationPayload`,
`InboundMessage`, `FollowUpDue`, `FormTemplate`, `FieldMapping`.

Énumérations : `ApplyRoute`, `ApplicationState`, `ErrorClass`, `EvidenceKind`, `HoldReason`,
`ValueSource`, `Outcome`, `DocumentRole`.

Tous les modèles en `model_config = ConfigDict(frozen=True, extra="forbid")`. Le `extra="forbid"` n'est
pas cosmétique : il fait échouer bruyamment le run le jour où la phase 2 ou 3 ajoute un champ, au lieu
de l'ignorer et de laisser partir une candidature incomplète.

### 13.4 Lien avec les artefacts de la phase 3

Exigence stricte, et c'est le point de couture le plus important du plan : **`DocumentBundle` doit
porter, pour chaque fichier, son chemin, son `sha256` et sa taille.** La phase 4 recalcule le hash
avant d'envoyer et refuse le lot en cas d'écart (`failed_permanent`, `reason_code =
bundle_hash_mismatch`). Deux accidents sont ainsi rendus impossibles : envoyer un PDF à moitié écrit
parce que la phase 3 n'avait pas fini de le produire, et envoyer le document d'une autre candidature
parce qu'un chemin de sortie est partagé. Juste après la vérification, les octets sont copiés dans le
magasin adressé par contenu, et c'est cette copie que le journal d'audit référence. La phase 3 peut
ensuite écraser ce qu'elle veut.

---

## 14. Les deux modes d'envoi

Même code, même machine à états, un seul état de différence.

**Mode autonome** (`--mode auto`) : les huit classes de rétention de la section 7.4 s'appliquent, tout
le reste part. Trois verrous pour activer l'envoi réel.

**Mode approbation** (`--mode approve`) : toute candidature passe par `awaiting_approval`. Dan reçoit
le `PlannedSubmission` rendu lisible — employeur, intitulé, lettre complète, réponses aux champs,
pièces jointes — et approuve ou rejette. Deux interfaces possibles : boutons en ligne Telegram, ou une
page HTML locale servie par `jobpipe review` avec des boutons d'approbation. L'approbation écrit
`approved_at` et fait passer en `queued` ; le run suivant, ou le même run en attente, envoie.

Ma recommandation est `--mode approve` par défaut, et l'argumentaire est en section 15. Le mode
autonome n'est pas bridé pour autant : il est complet, testé, et accessible. L'arbitrage est à Dan.

---

## 15. Franchise : ce qu'une candidature non relue vaut, et ce que ça coûte

### 15.1 Ce que voit un recruteur en 2026

Les chiffres que j'ai pu vérifier sont univoques. Une enquête auprès de plus de 4 100 entreprises dans
dix pays rapporte que **73 % des employeurs ont reçu, sur les six derniers mois, des CV générés par IA
contenant des informations fausses**, que 65 % constatent une hausse des candidats sous-qualifiés, et
qu'un quart reçoit plus de candidatures qu'il ne peut en traiter, avec en moyenne 9,24 jours par poste
passés à trier des candidatures hors sujet [tiers, enquête nommée]. Du côté des cabinets, plus de la
moitié des candidats déclarent postuler à vingt offres ou plus simultanément, certaines annonces
reçoivent plus de 600 candidatures en 24 heures dont environ une sur dix seulement est réellement
pertinente, et un employeur cité rapporte 420 candidatures en 72 heures pour un poste d'analyste junior
dont « environ 60 % » lues comme du texte templatisé par IA [tiers].

La réaction des employeurs est documentée et elle est structurelle, pas anecdotique : remplacement du
tri sur CV par des tests de mise en situation, introduction de courtes présentations vidéo ou audio,
filtres à deux niveaux qui **étiquettent** les CV suspects pour relecture humaine, et recours accru à
la cooptation [tiers]. Autrement dit : le CV et la lettre perdent leur pouvoir de discrimination, et
ce qui les remplace, c'est ce qu'un pipeline ne peut pas produire.

### 15.2 Ce que ça implique pour Dan en particulier

Une candidature non relue n'est pas « neutre mais moins bonne ». Elle tombe dans la pile que le
recruteur a appris à écarter, et elle y tombe sur des signaux que Dan ne contrôle pas depuis le
pipeline : la cadence, l'uniformité d'un paragraphe d'une annonce à l'autre, un horodatage à 3 heures
du matin, une accroche que le recruteur a déjà lue trois fois cette semaine.

Et le marché est petit. Junior IA/data, Région de Bruxelles-Capitale : les mêmes consultances, et
surtout le même petit nombre de recruteurs qui circulent entre elles. Être retenu comme « celui qui a
envoyé trois lettres presque identiques en un mois » n'est pas un coût ponctuel sur une candidature,
c'est un coût durable qui le suit d'un employeur à l'autre. L'automatisation ne rend pas cet effet plus
probable parce qu'elle est automatisée ; elle le rend plus probable parce qu'elle augmente le volume
et uniformise la forme.

Il y a enfin un argument spécifique à son dossier, et c'est le plus important. Son atout n'est pas un
CV standard de junior : c'est une polytechnique commencée, des années d'horeca, une société qu'il a
fondée et qu'il dirige. C'est précisément ce qui le distingue dans une pile de 400 candidatures, et
c'est précisément ce qu'un paragraphe généré aplatit, parce qu'un modèle produit la version moyenne de
cette histoire. Vingt candidatures non relues valent, en espérance, moins que cinq relues — pas parce
que la rédaction automatique est mauvaise, mais parce que la seule chose qui le sort de la pile est
celle qu'aucun modèle ne rédigera à sa place.

### 15.3 Ce que risque Dan concrètement

Classé par probabilité décroissante :

- **Candidature écartée au tri**, parce qu'elle ressemble à ce que l'employeur a décidé d'écarter.
  Quasi certain sur une partie du volume. Coût : nul en apparence, et c'est le piège — on ne voit pas
  la candidature perdue.
- **Blocage IP de sa connexion** par un fournisseur d'ATS après détection d'automatisation. Probable à
  moyen terme sur la route `hosted_form`. Coût : casse aussi le **scraping de la phase 1** depuis chez
  lui, donc perte d'une source. Effet collatéral souvent oublié.
- **Repérage par un recruteur** sur la forme ou la cadence. Peu probable par candidature, significatif
  sur un trimestre. Coût : réputation, non réversible, non mesurable.
- **Compte bloqué sur une plateforme.** Probable si l'espace personnel Actiris ou un portail
  d'employeur est automatisé. Les conditions d'Actiris que j'ai lues prévoient un blocage « immédiate
  et sans mise en demeure préalable » en cas de manquement — version employeurs, à confirmer côté
  chercheur d'emploi. Coût : potentiellement bien au-delà du pipeline.
- **Exposition pénale au titre de l'article 550bis.** Faible **si et seulement si** la règle de la
  section 2.3 est respectée : aucun appel à un endpoint exigeant une clé d'employeur, aucun
  contournement de captcha. Non négligeable si elle est franchie, et sans besoin de prouver une
  intention frauduleuse pour le §1er.

### 15.4 Pourquoi je recommande la file d'approbation

L'argument n'est pas moral, il est arithmétique. Le volume réellement automatisable est de deux à six
candidatures par semaine (section 11). Une approbation en un clic coûte quinze à trente secondes.
L'automatisation totale fait donc gagner **entre deux et trois minutes par semaine**, et engage en
contrepartie la seule ressource de ce projet qui ne se reconstruit pas.

Ce que la file d'approbation préserve, elle, est substantiel. La découverte, le filtrage, l'analyse de
la page, la rédaction, le remplissage du formulaire, la déduplication, le journal, les relances à
suivre : tout reste automatisé. Ce qui est automatisé, c'est 95 % du travail et 100 % de la charge
mentale. Ce qui reste humain, c'est la décision d'envoyer — et c'est aussi, accessoirement, la seule
étape où Dan peut encore ajouter la phrase qui fait la différence sur une candidature qui compte.

Une variante mérite d'être posée, parce qu'elle est peut-être le meilleur compromis : **approbation
obligatoire pour la liste `watchlist` et pour tout ce qui comporte une question libre, envoi autonome
pour le reste.** C'est exactement le mode autonome de la section 14 avec ses huit classes de rétention.
Si Dan choisit ce mode, il a déjà la file d'approbation là où elle compte, sans la supporter partout.

Je ne tranche pas pour lui. Le mode autonome est implémenté, testé et accessible. Mais je ne le mets
pas en défaut, parce qu'un défaut qui envoie est un défaut dont on découvre les effets après coup.

---

## Hypothèses sur les contrats des phases 2 et 3

- La phase 2 produit, pour chaque offre traitée, exactement un `ApplicationPlan` identifié par un `plan_id` stable et rattaché à l'offre par sa `canonical_key`.
- `ApplicationPlan` porte un champ de route dont les valeurs couvrent au moins `ats_api`, `hosted_form`, `email`, `account_portal`, `external` et `unknown`, et qui se projette sans ambiguïté sur l'énumération `ApplyRoute` de la phase 4.
- `ApplicationPlan` porte un verdict de faisabilité à au moins trois valeurs, dont une signifie « non automatisable », et la phase 4 traite toute valeur inconnue comme « non automatisable ».
- `ApplicationPlan` fournit l'URL de soumission ou l'adresse de destination sous une forme absolue et déjà déréférencée, sans redirection restant à suivre.
- `ApplicationPlan` énumère les champs obligatoires avec, pour chacun, une clé stable, un libellé, un type et le fait qu'il soit en texte libre ou à choix contraint.
- `ApplicationPlan` énumère séparément les questions propres à l'employeur, de sorte que la phase 4 peut détecter la présence d'au moins une question libre sans analyser le texte des champs.
- `ApplicationPlan` indique les formats de fichier acceptés et la taille maximale par fichier, ou déclare explicitement que l'information est inconnue.
- `ApplicationPlan` indique la langue détectée de l'annonce, pour que la rétention `language_mismatch` soit évaluable.
- `ApplicationPlan` liste les obstacles détectés avec des codes stables, dont au minimum un code pour « compte requis » et un pour « challenge anti-robot détecté ».
- `ApplicationPlan` fournit les coordonnées de contact du recruteur quand l'annonce les publie, et un champ vide sinon, jamais une valeur devinée.
- La phase 2 ne soumet jamais rien et n'écrit jamais dans les tables de la phase 4.
- La phase 3 produit, pour chaque `plan_id`, exactement un `DocumentBundle` identifié par un `document_bundle_id` stable.
- `DocumentBundle` porte, pour chaque fichier, son chemin absolu, son `sha256`, sa taille en octets et son type MIME, et la phase 4 refuse le lot si le hash recalculé diffère.
- `DocumentBundle` distingue les rôles de document par une valeur stable, couvrant au moins `cv` et `cover_letter`.
- `DocumentBundle` fournit les réponses générées aux champs et aux questions, indexées par la clé de champ du plan de la phase 2, et chacune porte la mention de sa provenance.
- `DocumentBundle` porte un indicateur permettant à la phase 4 de savoir si toute affirmation des documents est rattachable à une source du CV, et la phase 4 traite l'absence de cet indicateur comme « non rattachable ».
- `DocumentBundle` ne contient aucun emplacement non résolu, et la phase 4 refuse un lot dont un champ texte contient un marqueur de substitution non rempli.
- La phase 3 écrit ses fichiers sous des chemins qu'elle peut écraser à tout moment, raison pour laquelle la phase 4 en fait une copie figée avant tout envoi.
- La phase 1 garantit que `canonical_key` est stable dans le temps pour une même offre et identique entre deux sources décrivant la même offre.
- La phase 1 conserve le texte de l'annonce, ou à défaut la phase 4 le capture elle-même au moment de l'envoi.

---

## Arbitrages qui reviennent à Dan

**Mode d'envoi par défaut.** Options : autonome avec les huit classes de rétention ; file d'approbation
en un clic pour tout ; approbation limitée à la liste `watchlist` et aux questions libres.
*Ma recommandation :* la file d'approbation comme défaut, parce que le gain de l'autonomie totale est de
deux à trois minutes par semaine pour un risque de réputation non réversible. Le mode autonome reste
complet et disponible derrière les trois verrous.

**Canal de notification.** Options : fichier seul ; fichier plus Telegram ; fichier plus courriel.
*Ma recommandation :* fichier comme source de vérité plus Telegram pour la poussée. Contre le courriel :
il pollue la boîte sur laquelle les recruteurs répondent, et il couple l'alerting au transport qui
tombe. Pour Telegram : boutons d'action en ligne, donc approbation depuis le téléphone sans écrire
d'interface. Ce choix et le précédent sont liés et devraient être tranchés ensemble.

**Automatisation de la route `account_portal`.** Options : jamais ; avec approbation explicite par
candidature ; autonome avec identifiants stockés.
*Ma recommandation :* jamais. Seul cas où la perte est irréversible et où l'article 550bis §2 peut être
discuté. Le gain, 80 % du travail préparatoire, est déjà obtenu sans automatiser le clic final.

**Relances automatiques.** Options : automatiques à échéance ; brouillon préparé qu'il relit et envoie ;
simple rappel dans le digest.
*Ma recommandation :* brouillon préparé. Une relance templatisée détruit la seule valeur qu'une relance
possède, et sur ce marché elle est vue par des gens qui se parlent.

**Fenêtres du verrou employeur.** Options : les valeurs proposées, 1 par 14 jours et 3 par 90 jours ;
plus souples ; plus strictes.
*Ma recommandation :* commencer avec les valeurs proposées, les mesurer après un mois sur les données
réelles de `employer_key`. C'est le paramètre qui protège le plus sa réputation et celui qui frustre le
plus au quotidien ; il doit être réglé sur des chiffres observés, pas devinés.

**Cible quotidienne sous le plafond.** Options : utiliser les 20 ; cible souple de 5 à 8.
*Ma recommandation :* 5 à 8. Au-delà, on candidate aux offres classées `borderline`, c'est-à-dire à
celles pour lesquelles le filtre avait un doute.

**Ouverture de la route `ats_api` sur Recruitee.** Options : l'activer si `probe` confirme que
l'endpoint accepte une requête non authentifiée ; ne pas l'activer et passer par le formulaire hébergé.
*Ma recommandation :* l'activer si `probe` confirme. C'est la seule route offrant une confirmation forte,
et un endpoint de site de carrière ouvert est destiné aux candidats. Si `probe` renvoie 401 ou 403, on
ferme définitivement et on ne cherche pas de contournement.

**Format du magasin d'artefacts.** Options : fichiers adressés par contenu sur disque, index en base ;
blobs en base.
*Ma recommandation :* fichiers sur disque. Une base SQLite qui grossit de PDF et de captures d'écran
devient pénible à sauvegarder, et des fichiers restent lisibles même si le schéma change.

---

## Risques et questions ouvertes

**Le périmètre réel dépend d'une mesure qui n'a pas encore été faite.** La distribution des valeurs de
`apply_path` sur le flux réel détermine tout le reste de ce plan. Si 50 % des offres sont `external` ou
`unknown`, les routes `ats_api` et `hosted_form` représentent quelques candidatures par semaine. **À
faire avant d'écrire une ligne de la phase 4 : un comptage de `apply_path` sur le stock actuel de
`job`.** C'est une requête, et elle peut modifier substantiellement l'ordre des priorités.

**La présence de captcha sur les boards Greenhouse et Workable n'est pas vérifiée.** Je n'ai trouvé
aucune source primaire. Si Turnstile est généralisé, la route `hosted_form` s'effondre et avec elle la
plus grosse part du volume automatisable. À mesurer par `probe`, en lecture, avant de construire les
templates.

**Les conditions d'utilisation côté chercheur d'emploi d'Actiris n'ont pas été localisées.** Celles que
j'ai lues sont la version employeurs. À lire avant toute automatisation touchant l'espace personnel.

**L'endpoint Recruitee est documenté sans mention d'authentification, ce qui n'est pas la même chose
qu'une authentification documentée comme absente.** L'absence de mention peut refléter une page de
référence incomplète. `probe` doit le trancher, et un 401 ferme le sujet.

**Le chemin exact de l'endpoint « Apply to a posting » de Lever n'a pas été confirmé**, mais
l'authentification par clé l'exclut de toute façon. Mentionné pour mémoire, pas pour exploration.

**La route `hosted_form` est une dette de maintenance permanente.** Chaque template casse quand un
fournisseur change son DOM, sans avertissement et sans corrélation avec les releases du pipeline.
Le coût de maintenance est à mon sens le facteur le plus sous-estimé de cette phase, et il peut à lui
seul justifier de concentrer l'effort sur `email` et sur la préparation de dossiers pour envoi manuel.

**L'alignement SPF/DKIM/DMARC de sa configuration d'envoi est inconnu.** Un courriel mal aligné part en
indésirable sans aucun signal observable, et la route `email` est celle sur laquelle je compte le plus.
À vérifier avant le premier envoi réel, avec un test d'alignement vers une adresse de contrôle.

**Le sous-adressage `+` dépend de son fournisseur de messagerie.** Si le `+` n'est pas accepté, la
corrélation des réponses retombe sur les en-têtes de thread et le domaine de l'expéditeur, ce qui est
nettement moins fiable. À vérifier tôt, car cela conditionne la qualité de tout le suivi post-envoi.

**La détection d'une affirmation non rattachable dépend entièrement de la phase 3.** Si la phase 3 ne
fournit pas cet indicateur, la rétention `ungrounded_claim` devient inopérante et la règle « la couche
LLM n'invente jamais de données » perd son point de contrôle au moment qui compte. À remonter à l'agent
qui planifie la phase 3.

**La fenêtre de grâce de 24 heures sans rebond est un paramètre arbitraire.** Les DSN de certains
serveurs arrivent après plusieurs jours. Conséquence acceptée : une candidature peut passer en
`submitted` puis recevoir un rebond, ce qui doit être traité comme une transition
`submitted → failed_permanent` légale et notifiée. À prévoir dans la machine à états.

**Un employeur peut utiliser plusieurs ATS simultanément**, ce qui casse la dérivation de
`employer_key` par identité ATS et produit deux identités pour un même interlocuteur. La table
`employer_alias` est le filet, mais elle demande une action manuelle, donc le doublon se voit après
coup. Pas de solution automatique satisfaisante à proposer.
