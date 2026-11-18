# jobpipe

jobpipe cherche des **offres juniors en IA, data et machine learning à Bruxelles** (Région de
Bruxelles-Capitale) et rassemble celles qui te correspondent sur une seule page.
Ensuite, il écrit pour chaque offre **un CV d'une page, en PDF et en Word**, adapté à l'offre, à
partir d'un fichier où tu décris ton parcours une fois pour toutes.

Tout tourne sur ton PC, sans carte graphique. jobpipe n'envoie rien aux employeurs : il lit des
offres publiques et écrit des fichiers, c'est toi qui postules.

**Sommaire**

1. [Prérequis](#1-prérequis)
2. [Installation](#2-installation) : [avec Docker](#chemin-a--avec-docker) ou [sans Docker](#chemin-b--sans-docker)
3. [Première configuration](#3-première-configuration)
4. [Trouver des offres](#4-trouver-des-offres)
5. [Générer tes CV](#5-générer-tes-cv)
6. [Personnaliser la recherche](#6-personnaliser-la-recherche)
7. [Mettre à jour jobpipe](#7-mettre-à-jour-jobpipe)
8. [Problèmes fréquents](#8-problèmes-fréquents)

---

## 1. Prérequis

Choisis **un** des deux chemins et suis-le jusqu'au bout :

| | Chemin A : avec Docker | Chemin B : sans Docker |
|---|---|---|
| Pour qui | Docker Desktop est déjà installé sur ton PC | Tu préfères installer Python |
| À installer | [Git](https://git-scm.com/downloads) et [Docker Desktop](https://www.docker.com/products/docker-desktop/) | [Git](https://git-scm.com/downloads) et [Python 3.11 ou plus récent](https://www.python.org/downloads/) |
| Commandes | `docker compose run --rm jobpipe ...` | `jobpipe ...` |

Dans les deux cas, il te faut aussi un **éditeur de texte** pour remplir ton profil (VS Code,
Notepad++, ou le Bloc-notes). Pas besoin de carte graphique, ni de LibreOffice, ni de Word.

Toutes les commandes de ce guide se tapent dans un terminal : **PowerShell** sur Windows (menu
Démarrer, tape « PowerShell »), **Terminal** sur Mac et Linux.

---

## 2. Installation

### Chemin A : avec Docker

1. Lance **Docker Desktop** et attends qu'il indique « Engine running ».
2. Dans le terminal, télécharge le projet et entre dans son dossier :

   ```
   git clone <adresse-du-dépôt> jobpipe
   cd jobpipe
   ```

3. Construis l'image (une minute environ la première fois) :

   ```
   docker compose build
   ```

C'est tout. Avec Docker, **chaque commande `jobpipe` de ce guide s'écrit
`docker compose run --rm jobpipe` suivi de la suite**. Par exemple `jobpipe cv` devient :

```
docker compose run --rm jobpipe cv
```

Lance toujours ces commandes depuis le dossier `jobpipe`. Ce dossier est partagé avec Docker : les
offres, les rapports et les CV y sont écrits, et tu les ouvres normalement depuis ton PC. Dans les
messages de jobpipe, `/work` désigne ce dossier : `/work/documents/...` correspond à
`jobpipe\documents\...` sur ton PC.

### Chemin B : sans Docker

Vérifie d'abord que Python est installé : `py --version` (Windows) ou `python3 --version` (Mac,
Linux) doit afficher 3.11 ou plus. Sur Windows, à l'installation de Python, coche
« Add python.exe to PATH ».

**Windows (PowerShell)** :

```
git clone <adresse-du-dépôt> jobpipe
cd jobpipe
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

L'installation prend une à deux minutes. Un message final « A new release of pip is available »
n'est pas une erreur : ignore-le.

Si PowerShell refuse la commande `Activate.ps1` (« l'exécution de scripts est désactivée »), tape
une seule fois `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, réponds `O` (ou `Y`), puis
relance les deux dernières lignes : `.venv\Scripts\Activate.ps1` puis `pip install -e .`.

**Mac et Linux** :

```
git clone <adresse-du-dépôt> jobpipe
cd jobpipe
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

(Sur Ubuntu ou Debian, si `venv` manque : `sudo apt install python3-venv`.)

Quand l'environnement est actif, la ligne du terminal commence par `(.venv)`. **À chaque nouveau
terminal**, place-toi dans le dossier `jobpipe` et réactive-le avant d'utiliser jobpipe :

```
cd jobpipe
.venv\Scripts\Activate.ps1        (Windows)
source .venv/bin/activate         (Mac, Linux)
```

---

## 3. Première configuration

### Ton adresse de contact (obligatoire)

```
jobpipe init --contact ton.adresse@example.org
```

(avec Docker : `docker compose run --rm jobpipe init --contact ton.adresse@example.org`)

Mets **ta propre adresse e-mail** : jobpipe l'indique aux sites qu'il consulte, pour qu'ils puissent
contacter la personne responsable du trafic si besoin. Elle est enregistrée dans le fichier `.env` du
dossier, qui reste sur ton PC.

La commande affiche `contact saved in ...` puis quelques lignes techniques (dossiers et base de
données créés, liste de tables) : c'est normal, il n'y a rien à faire.

### Une clé Adzuna (facultatif)

Sans clé, jobpipe fonctionne déjà avec ses autres sources. Une clé gratuite Adzuna ajoute des offres :

1. Crée un compte sur https://developer.adzuna.com (bouton « Register »).
2. Dans ton tableau de bord, copie l'**Application ID** et l'**Application Key**.
3. Ouvre le fichier `.env` du dossier `jobpipe` avec ton éditeur et ajoute deux lignes :

   ```
   ADZUNA_APP_ID=ton_app_id
   ADZUNA_APP_KEY=ta_app_key
   ```

### Ton profil

Le profil est le fichier qui décrit ton parcours. Le CV ne contient **que des phrases de ton
profil** : rien n'est inventé, donc écris chaque phrase exactement comme tu veux la lire sur le CV.

Copie l'exemple (le profil fictif de « Sam Exemple ») :

```
copy profile\profile.example.yaml profile\profile.yaml      (Windows)
cp profile/profile.example.yaml profile/profile.yaml        (Mac, Linux)
```

Ouvre `profile/profile.yaml` dans ton éditeur et remplace tout par tes informations, rubrique par
rubrique. Ce fichier reste sur ton PC (Git l'ignore).

Trois règles d'écriture valent pour tout le fichier :

- l'indentation se fait avec des **espaces**, jamais des tabulations : garde l'alignement de
  l'exemple ;
- les dates sont des mois entre guillemets : `"2025-09"` ; `end: null` veut dire « en cours » ;
- chaque phrase existe en français (`fr:`) et en anglais (`en:`), car le CV suit la langue de
  l'offre.

| Rubrique | Ce que tu y mets |
|---|---|
| `identity` | Prénom, nom, ville, code pays (`BE`), e-mail, téléphone au format international sans espace (`"+32470123456"`), et tes liens LinkedIn / GitHub. Ils forment l'en-tête du CV. |
| `languages` | Tes langues avec leur niveau européen (`A1` à `C2`, en majuscules) pour la compréhension, l'oral et l'écrit. `is_native: true` pour ta langue maternelle. `can_author_documents: false` sur une langue que tu ne maîtrises pas assez pour qu'un CV soit écrit dedans. |
| `education` | Tes études. `completed: true` seulement si le diplôme est obtenu, avec son nom dans `credential` ; sinon `completed: false` et `credential: null`. La ligne imprimée sur le CV est celle de `phrasings` (par exemple « 3e année en cours »). |
| `experience` | Tes emplois et jobs étudiants : employeur (`organisation`), fonction en français et en anglais (`role_fr`, `role_en`), dates, et les puces (`bullets`). |
| `projects` | Tes projets (cours, perso, hackathon...) : nom, technologies (`stack`), `tags`, dates et puces. C'est souvent la rubrique la plus importante pour un premier poste. |
| `metrics` | Chaque chiffre que tu cites dans une puce (« 3 000 tickets », « 40 % »). Un chiffre non déclaré ici rend le CV « à vérifier ». |
| `skills` | Tes compétences, chacune reliée aux expériences ou projets qui la prouvent (`evidence_refs`). |
| `cv_titles` | Les titres possibles sous ton nom (par exemple « IA et machine learning », « data engineering »). jobpipe choisit celui qui colle le mieux à l'offre. |
| `never_claim` | Ce que le CV ne doit jamais affirmer (un diplôme pas encore obtenu, une certification...). Garde les lignes de l'exemple si elles te concernent. |
| `claims_bank` | Laisse `[]` : le CV ne s'en sert pas. |

**Les puces** (`bullets`) : une puce = une ligne du CV. Chacune a un identifiant unique
(`bullet_id`), une phrase `fr` et une phrase `en`. `droppable: false` la garde sur tous les CV ;
`droppable: true` permet à jobpipe de la retirer si la page est pleine. Trois ou quatre puces par
entrée suffisent : le CV doit tenir sur une page A4. Les identifiants (`entry_id`, `bullet_id`,
`skill_id`...) peuvent être n'importe quel mot, du moment que chacun est unique et que les
références (`depends_on`, `evidence_refs`, `metrics`) pointent vers des identifiants qui existent.

**Les `tags`** décident de ce qui passe en premier : jobpipe repère les mots-clés de l'offre (Python,
SQL, machine learning...) et met en tête les projets, puces, compétences et le titre qui portent les
mêmes tags. Utilise les mots de cette liste, ce sont ceux que jobpipe reconnaît dans les offres :

```
python sql spark airflow dbt docker kubernetes aws azure gcp pytorch tensorflow scikit_learn
pandas numpy llm rag langchain hugging_face nlp computer_vision machine_learning deep_learning
mlops etl kafka git ci_cd terraform java scala typescript snowflake databricks tableau power_bi
looker excel statistics data_visualization fastapi flask
```

Exemple : un projet tagué `[machine_learning, python, pandas]` remonte en tête pour une offre de
data scientist qui demande Python et pandas. Un autre mot est accepté, mais il ne correspondra à
aucune offre.

Si le profil contient une erreur, `jobpipe cv` l'indique avec l'endroit exact et la correction
attendue, par exemple :

```
Profile error: /work/profile/profile.yaml has 1 problem(s):
  - identity.phone_e164: expected international format without spaces or dots, for example +32470123456 (found '0470 00 00 00')
See 'Ton profil' in README.md; the example is profile/profile.example.yaml.
```

Ici, le téléphone doit s'écrire `"+32470000000"`.

---

## 4. Trouver des offres

```
jobpipe run --no-audit
```

(avec Docker : `docker compose run --rm jobpipe run --no-audit`)

Le **premier passage prend environ 5 à 15 minutes** : jobpipe interroge Actiris (la source
principale) lentement, par politesse envers le site. Les passages suivants sont plus courts, car les
offres déjà vues sont sautées. Relance la commande tous les jours ou tous les deux jours : de
nouvelles offres arrivent chaque jour.

Une offre est retenue si elle est à Bruxelles (ou en télétravail pour un employeur présent en
Belgique), dans l'IA, la data ou le machine learning, accessible à un junior, rédigée en français ou
en anglais, et récente. Un premier passage réel (avec Docker, sans clé Adzuna ; liste raccourcie)
ressemble à ceci :

```
polling the sources, one line per source as it finishes (the first run can take several minutes)
  recruitee: seen=33 new=33 targets=1
  greenhouse: seen=173 new=170 targets=2
  ashby: seen=147 new=134 targets=2
  actiris: seen=222 new=154 targets=18
  adzuna: skipped (no Adzuna key, which is optional; add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env to include Adzuna)
run 1: OK
filtered 696 postings: 15 survivors, 681 rejected
audit: skipped (--no-audit)
new offers that passed the filters (15):
  - AI & Data Engineer M/V/X | employer not stated | 1210 Saint-Josse-ten-Noode
    https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=...   (id 29dddad40b5d341b)
  - Business Intelligence Consultant M/V/X | employer not stated | 1210 Saint-Josse-ten-Noode [to check]
    https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=...   (id 0b1f611952db5233)
  ...
report of this run: /work/data/reports/run-2026-11-18-2144-1.html
all 15 offers kept so far: /work/data/reports/offers.html  (or: jobpipe offers)
next: jobpipe cv            (a tailored CV for each offer; see README)
```

Sur des centaines d'annonces lues, seules quelques-unes passent les filtres : c'est voulu, la liste
ne garde que ce qui vise un junior à Bruxelles.

**Pour voir toutes les offres retenues**, ouvre dans ton navigateur (double-clic) le fichier
`data/reports/offers.html` du dossier `jobpipe`. Il cumule tous les passages, les plus récentes
en haut, avec le lien vers chaque annonce. `[to check]` signale une offre à lire de près (par
exemple 1 à 2 ans d'expérience demandés, ce qui reste souvent négociable).

La même liste s'affiche dans le terminal avec `jobpipe offers`, avec l'identifiant de chaque offre.

---

## 5. Générer tes CV

Une fois ton profil rempli et au moins une offre retenue :

```
jobpipe cv
```

(avec Docker : `docker compose run --rm jobpipe cv`)

Pour chaque offre (10 à la fois ; `jobpipe cv --limit 30` pour en faire plus), jobpipe lit la page de
l'offre, choisit le titre, l'ordre des projets et des compétences qui collent à l'offre, et écrit un
CV A4 d'une page. Le CV est en français pour une offre en français, en anglais sinon. Le terminal
affiche, pour chaque offre, l'emplacement des fichiers. Exemple réel avec le profil fictif de
l'exemple (avec Docker, liste raccourcie) :

```
reading the page of 10 offer(s) once, to learn the language and the skills asked...
CV ready: AI & Data Engineer M/V/X | employer not stated
    offer: https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=...
    PDF:   /work/documents/29dddad40b5d341b/3/outbox/Exemple_Sam_CV_AI_Data_Engineer.pdf
    Word:  /work/documents/29dddad40b5d341b/3/cv.docx  (editable copy)
    note:  the offer asks for Dutch; your CV is in English
CV ready: Business Intelligence Consultant M/V/X | employer not stated
    offer: https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=...
    PDF:   /work/documents/0b1f611952db5233/3/outbox/Exemple_Sam_CV_Business_Intelligence_Co.pdf
    Word:  /work/documents/0b1f611952db5233/3/cv.docx  (editable copy)
...
10 CV(s) ready, 0 to check, 0 failed
PDFs made by the built-in renderer (LibreOffice not found); the Word file is the editable version
5 more offer(s) waiting: run the command again or raise --limit
```

Sans Docker, les chemins commencent par l'emplacement de ton dossier `jobpipe`, par exemple
`D:\cours\jobpipe\documents\...` sur Windows. Avec Docker, `/work/documents/...` est le dossier
`documents` de ton dossier `jobpipe`.

- **Le PDF** est prêt à être envoyé. Il est rangé dans
  `documents/<identifiant de l'offre>/<numéro>/outbox/` et porte ton nom et celui de l'employeur
  (ou le titre de l'offre). Le plus simple est de copier le chemin affiché après `PDF:`.
- **Le fichier Word** (`cv.docx`, dans le dossier `<numéro>`, juste au-dessus de `outbox`) est une
  copie modifiable, si tu veux retoucher un détail à la main.
- **Le `<numéro>`** change à chaque génération : quand une offre a plusieurs dossiers numérotés, le
  CV à jour est dans celui qui porte le **plus grand numéro** (les anciens restent, tu peux les
  supprimer).
- **Un CV pour une seule offre** : `jobpipe cv --job-id <id>`, avec l'identifiant affiché par
  `jobpipe offers`.
- **`note:`** donne une information sur l'offre (par exemple qu'elle demande le néerlandais), pas
  un problème du CV.
- **`more offer(s) waiting`** : relance `jobpipe cv` pour faire les CV suivants.

**« CV to check » (à vérifier)** : le CV est quand même écrit, mais jobpipe préfère que tu le relises
avant de l'envoyer. La raison est affichée sous `why:`. Les cas habituels :

- *longer than one page* : raccourcis des puces ou passe-en davantage en `droppable: true` ;
- *a claim your profile does not back* : la phrase citée sous `why:` contient un chiffre absent de
  `metrics`, un diplôme non déclaré obtenu, un « nous » ou un mot comme « expert ». Reformule-la
  dans ton profil.

**Après une modification du profil**, relance simplement `jobpipe cv` (et encore une fois tant que
`more offer(s) waiting` s'affiche) : les CV sont refaits avec la nouvelle version, dans un nouveau
dossier numéroté. `jobpipe cv --force` refait tous les CV, même sans changement.

---

## 6. Personnaliser la recherche

Ne modifie pas les fichiers du dossier `config/` : ils sont remplacés à chaque mise à jour. Mets tes
réglages dans un dossier `local/`, que Git ignore. Le dossier `local.example/` contient des modèles
commentés ; copie celui dont tu as besoin :

```
mkdir local
copy local.example\companies.yaml local\      (Windows)
cp local.example/companies.yaml local/        (Mac, Linux)
```

Chaque fichier de `local/` complète le fichier du même nom dans `config/` : tu n'y écris que ce que
tu changes.

**Ajouter une entreprise** : `local/companies.yaml`

```yaml
companies:
  - { slug: mon-employeur, name: Mon Employeur, ats: greenhouse, ats_slug: monemployeur }
```

`ats` est le service qui héberge la page carrières de l'entreprise (`greenhouse`, `lever`, `ashby`,
`recruitee`, `smartrecruiters`, `workable`, `personio`) et `ats_slug` le nom qui apparaît dans son
adresse : `boards.greenhouse.io/<ats_slug>`, `jobs.lever.co/<ats_slug>`,
`jobs.ashbyhq.com/<ats_slug>`, `<ats_slug>.recruitee.com`... Si tu ne connais que l'adresse de la
page carrières, jobpipe peut la reconnaître :

```
jobpipe company add "Mon Employeur" --careers-url https://...
jobpipe company resolve --slug mon-employeur
```

`jobpipe company list` affiche les entreprises suivies.

**Changer les mots-clés de recherche ou les langues acceptées** : `local/taxonomy.yaml` (copie de
`local.example/taxonomy.yaml`). Enlève le `#` devant les lignes à activer. Une liste écrite là
remplace la liste d'origine : recopie-la depuis `config/taxonomy.yaml` (rubrique `queries`), puis
ajoute ou retire des mots-clés. Pour accepter les offres en néerlandais, mets
`accepted: [fr, en, nl]`.

---

## 7. Mettre à jour jobpipe

Depuis le dossier `jobpipe` :

```
git pull
```

puis :

- avec Docker : `docker compose build`
- sans Docker (environnement activé) : `pip install -e .`

Tes fichiers personnels (`.env`, `profile/profile.yaml`, `local/`, `data/`, `documents/`) ne sont
pas suivis par Git : `git pull` ne les touche pas et ne crée pas de conflit, tant que tu n'as pas
modifié les fichiers du projet eux-mêmes (voir la FAQ si `git pull` refuse).

---

## 8. Problèmes fréquents

**`jobpipe` n'est pas reconnu** (sans Docker) : l'environnement n'est pas activé. Place-toi dans le
dossier `jobpipe` et lance `.venv\Scripts\Activate.ps1` (Windows) ou `source .venv/bin/activate`
(Mac, Linux).

**`py` ou `python3` n'est pas reconnu** : Python n'est pas installé, ou pas dans le PATH.
Réinstalle-le depuis python.org en cochant « Add python.exe to PATH », puis ouvre un nouveau
terminal.

**`failed to connect to the docker API` ou `Cannot connect to the Docker daemon`** : Docker Desktop
n'est pas lancé. Ouvre-le, attends « Engine running », puis relance la commande.

**`configuration directory not found`** : tu n'es pas dans le dossier `jobpipe`. Fais `cd jobpipe`
(ou le chemin complet du dossier) puis relance.

**Message qui parle de `contact`** : l'adresse de contact manque. Lance
`jobpipe init --contact ton.adresse@example.org`.

**`Profile error`** : le message donne la ligne à corriger (par exemple `projects[2].bullets[1].en`
= la 1re puce du 2e projet, phrase anglaise) et le format attendu. Compare avec
`profile/profile.example.yaml`.

**`no offer kept yet`** : lance d'abord `jobpipe run --no-audit`, puis `jobpipe cv`.

**Aucune nouvelle offre retenue** : c'est normal certains jours. Relance le lendemain, ajoute une
clé Adzuna, ou ajoute des entreprises et des mots-clés (section 6).

**Le CV ne se met pas à jour** : ferme le PDF s'il est ouvert dans un lecteur (Windows bloque un
fichier ouvert), puis relance `jobpipe cv --force`.

**`git pull` refuse à cause de modifications locales** : tu as modifié un fichier du projet
(souvent dans `config/`). `git status` montre lequel. Recopie tes changements dans `local/`
(section 6), puis annule la modification avec `git checkout -- <fichier>` et relance `git pull`.

**Linux avec Docker : les fichiers créés appartiennent à `root`** : ajoute
`--user "$(id -u):$(id -g)"` après `run`, par exemple
`docker compose run --rm --user "$(id -u):$(id -g)" jobpipe cv`.

**La ligne `actiris` signale une erreur** : le site d'Actiris ne répondait pas. Les autres sources
ont quand même été lues ; relance `jobpipe run --no-audit` plus tard. Si l'erreur persiste plusieurs
jours et propose d'installer un navigateur (sans Docker uniquement) : `pip install -e ".[browser]"`,
`playwright install chromium`, puis `jobpipe actiris-discover`.
