# jobpipe

jobpipe helps you apply to jobs **in any field** (data, marketing, sales, accounting,
logistics...). It does two things:

- it **finds junior job offers in Brussels** (the Brussels-Capital Region) in the field you choose
  and gathers the ones that match you on a single page;
- for each offer, it writes **a one-page CV, as a PDF and as a Word file**, tailored to the offer,
  from a file where you describe your background once and for all. You can also give it the
  address of any offer you found yourself.

Everything runs on your PC, with no graphics card. jobpipe sends nothing to employers: it reads
public job offers and writes files; you are the one who applies.

Out of the box, the search looks for AI, data and machine-learning offers. To search another
field, or to make a CV for an offer you found yourself, see
[section 7](#7-use-it-for-any-kind-of-job).

**Contents**

1. [Requirements](#1-requirements)
2. [Installation](#2-installation): [with Docker](#path-a-with-docker) or [without Docker](#path-b-without-docker)
3. [First setup](#3-first-setup)
4. [Find offers](#4-find-offers)
5. [Generate your CVs](#5-generate-your-cvs)
6. [Customise the search](#6-customise-the-search)
7. [Use it for any kind of job](#7-use-it-for-any-kind-of-job): [a CV for any offer](#a-cv-for-any-offer-by-its-address), [search another field](#search-another-field-automatically), [tags for your field](#tags-for-your-field)
8. [Update jobpipe](#8-update-jobpipe)
9. [Common problems](#9-common-problems)

---

## 1. Requirements

Pick **one** of the two paths and follow it to the end:

| | Path A: with Docker | Path B: without Docker |
|---|---|---|
| For whom | Docker Desktop is already installed on your PC | You prefer to install Python |
| To install | [Git](https://git-scm.com/downloads) and [Docker Desktop](https://www.docker.com/products/docker-desktop/) | [Git](https://git-scm.com/downloads) and [Python 3.11 or newer](https://www.python.org/downloads/) |
| Commands | `docker compose run --rm jobpipe ...` | `jobpipe ...` |

In both cases you also need a **text editor** to fill in your profile (VS Code, Notepad++, or
Notepad). No graphics card, LibreOffice or Word is needed.

All the commands in this guide are typed in a terminal: **PowerShell** on Windows (Start menu,
type "PowerShell"), **Terminal** on Mac and Linux.

---

## 2. Installation

### Path A: with Docker

1. Start **Docker Desktop** and wait until it shows "Engine running".
2. In the terminal, download the project and go into its folder:

   ```
   git clone <repository-url> jobpipe
   cd jobpipe
   ```

3. Build the image (about one minute the first time):

   ```
   docker compose build
   ```

That's it. With Docker, **every `jobpipe` command in this guide is written
`docker compose run --rm jobpipe` followed by the rest**. For example `jobpipe cv` becomes:

```
docker compose run --rm jobpipe cv
```

Always run these commands from the `jobpipe` folder. This folder is shared with Docker: the
offers, the reports and the CVs are written there, and you open them as usual from your PC. In
jobpipe's messages, `/work` means this folder: `/work/documents/...` is
`jobpipe\documents\...` on your PC.

### Path B: without Docker

First check that Python is installed: `py --version` (Windows) or `python3 --version` (Mac,
Linux) must show 3.11 or higher. On Windows, when you install Python, tick
"Add python.exe to PATH".

**Windows (PowerShell)**:

```
git clone <repository-url> jobpipe
cd jobpipe
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

The installation takes one to two minutes. A final message "A new release of pip is available"
is not an error: ignore it.

If PowerShell refuses the `Activate.ps1` command ("running scripts is disabled on this system"),
type `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer `Y`, then run the last two
lines again: `.venv\Scripts\Activate.ps1` then `pip install -e .`.

**Mac and Linux**:

```
git clone <repository-url> jobpipe
cd jobpipe
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

(On Ubuntu or Debian, if `venv` is missing: `sudo apt install python3-venv`.)

When the environment is active, the terminal line starts with `(.venv)`. **In every new
terminal**, go to the `jobpipe` folder and activate it again before using jobpipe:

```
cd jobpipe
.venv\Scripts\Activate.ps1        (Windows)
source .venv/bin/activate         (Mac, Linux)
```

---

## 3. First setup

### Your contact address (required)

```
jobpipe init --contact your.address@example.org
```

(with Docker: `docker compose run --rm jobpipe init --contact your.address@example.org`)

Use **your own e-mail address**: jobpipe gives it to the sites it visits, so that they can contact
the person responsible for the traffic if needed. It is saved in the `.env` file of the folder,
which stays on your PC.

The command prints `contact saved in ...` followed by a few technical lines (folders and database
created, list of tables): this is normal, there is nothing to do.

### An Adzuna key (optional)

Without a key, jobpipe already works with its other sources. A free Adzuna key adds more offers:

1. Create an account on https://developer.adzuna.com ("Register" button).
2. In your dashboard, copy the **Application ID** and the **Application Key**.
3. Open the `.env` file of the `jobpipe` folder in your editor and add two lines:

   ```
   ADZUNA_APP_ID=your_app_id
   ADZUNA_APP_KEY=your_app_key
   ```

### Your profile

The profile is the file that describes your background. The CV contains **only sentences from your
profile**: nothing is made up, so write each sentence exactly the way you want to read it on the CV.

Copy the example (the fictional profile of "Sam Exemple"):

```
copy profile\profile.example.yaml profile\profile.yaml      (Windows)
cp profile/profile.example.yaml profile/profile.yaml        (Mac, Linux)
```

Open `profile/profile.yaml` in your editor and replace everything with your own information,
section by section. This file stays on your PC (Git ignores it).

Three writing rules apply to the whole file:

- indent with **spaces**, never tabs: keep the alignment of the example;
- dates are months between quotes: `"2025-09"`; `end: null` means "ongoing";
- every sentence exists in French (`fr:`) and in English (`en:`), because the CV follows the
  language of the offer.

| Section | What you put in it |
|---|---|
| `identity` | First name, last name, city, country code (`BE`), e-mail, phone number in international format without spaces (`"+32470123456"`), and your LinkedIn / GitHub links. They make up the CV header. |
| `languages` | Your languages with their European level (`A1` to `C2`, in capitals) for comprehension, speaking and writing. `is_native: true` for your mother tongue. `can_author_documents: false` on a language you do not master well enough for a CV to be written in it. |
| `education` | Your studies. `completed: true` only if the diploma is obtained, with its name in `credential`; otherwise `completed: false` and `credential: null`. The line printed on the CV is the one in `phrasings` (for example "third year in progress"). |
| `experience` | Your jobs and student jobs: employer (`organisation`), job title in French and in English (`role_fr`, `role_en`), dates, and the bullet points (`bullets`). |
| `projects` | Your projects (course, personal, hackathon...): name, technologies (`stack`), `tags`, dates and bullet points. This is often the most important section for a first job. |
| `metrics` | Every number you quote in a bullet point ("3,000 tickets", "40 %"). A number that is not declared here makes the CV "to check". |
| `skills` | Your skills, each one linked to the experiences or projects that prove it (`evidence_refs`). |
| `cv_titles` | The possible titles under your name (for example "AI and machine learning", "data engineering"). jobpipe picks the one that best fits the offer. |
| `never_claim` | What the CV must never state (a diploma not yet obtained, a certification...). Keep the lines of the example if they apply to you. |
| `claims_bank` | Leave `[]`: the CV does not use it. |

**Bullet points** (`bullets`): one bullet = one line of the CV. Each one has a unique identifier
(`bullet_id`), a `fr` sentence and an `en` sentence. `droppable: false` keeps it on every CV;
`droppable: true` lets jobpipe remove it if the page is full. Three or four bullets per entry are
enough: the CV must fit on one A4 page. The identifiers (`entry_id`, `bullet_id`,
`skill_id`...) can be any word, as long as each one is unique and the references (`depends_on`,
`evidence_refs`, `metrics`) point to identifiers that exist.

**The `tags`** decide what comes first: jobpipe spots the keywords of the offer (Python, SQL,
machine learning...) and puts first the projects, bullets, skills and title that carry the same
tags. Use the words from this list; they are the ones jobpipe recognises in offers:

```
python sql spark airflow dbt docker kubernetes aws azure gcp pytorch tensorflow scikit_learn
pandas numpy llm rag langchain hugging_face nlp computer_vision machine_learning deep_learning
mlops etl kafka git ci_cd terraform java scala typescript snowflake databricks tableau power_bi
looker excel statistics data_visualization fastapi flask
```

Example: a project tagged `[machine_learning, python, pandas]` moves to the top for a data
scientist offer that asks for Python and pandas. Any other word is accepted, but it will not match
any offer.

If the profile contains a mistake, `jobpipe cv` tells you the exact place and the expected fix,
for example:

```
Profile error: /work/profile/profile.yaml has 1 problem(s):
  - identity.phone_e164: expected international format without spaces or dots, for example +32470123456 (found '0470 00 00 00')
See 'Your profile' in README.md; the example is profile/profile.example.yaml.
```

Here, the phone number must be written `"+32470000000"`.

---

## 4. Find offers

```
jobpipe run --no-audit
```

(with Docker: `docker compose run --rm jobpipe run --no-audit`)

The **first run takes about 5 to 15 minutes**: jobpipe queries Actiris (the main source) slowly,
out of politeness towards the site. Later runs are shorter, because offers already seen are
skipped. Run the command again every day or every other day: new offers arrive every day.

An offer is kept if it is in Brussels (or remote for an employer based in Belgium), in AI, data
or machine learning, open to a junior, written in French or English, and recent. A real first run
(with Docker, without an Adzuna key; shortened list) looks like this:

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

Out of hundreds of postings read, only a few pass the filters: this is on purpose, the list only
keeps what targets a junior in Brussels.

**To see all the kept offers**, open the file `data/reports/offers.html` of the `jobpipe` folder
in your browser (double-click). It adds up all the runs, newest first, with the link to each
posting. `[to check]` marks an offer to read carefully (for example 1 to 2 years of experience
asked, which is often negotiable).

The same list is shown in the terminal with `jobpipe offers`, with the identifier of each offer.

---

## 5. Generate your CVs

Once your profile is filled in and at least one offer is kept:

```
jobpipe cv
```

(with Docker: `docker compose run --rm jobpipe cv`)

For each offer (10 at a time; `jobpipe cv --limit 30` to do more), jobpipe reads the offer's page,
picks the title and the order of projects and skills that fit the offer, and writes a one-page A4
CV. The CV is in French for an offer in French, in English otherwise. For each offer, the terminal
shows where the files are. A real example with the fictional example profile (with Docker,
shortened list):

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

Without Docker, the paths start with the location of your `jobpipe` folder, for example
`D:\courses\jobpipe\documents\...` on Windows. With Docker, `/work/documents/...` is the
`documents` folder of your `jobpipe` folder.

- **The PDF** is ready to send. It is stored in
  `documents/<offer id>/<number>/outbox/` and its name contains your name and the employer's name
  (or the offer title). The easiest way is to copy the path shown after `PDF:`.
- **The Word file** (`cv.docx`, in the `<number>` folder, just above `outbox`) is an editable
  copy, if you want to touch up a detail by hand. It is set in Calibri, which Word and
  LibreOffice (through the equivalent Carlito) have; the PDF made without LibreOffice embeds
  Carlito, shipped with jobpipe (SIL Open Font License, see `src/jobpipe/documents/fonts/OFL.txt`),
  so both look the same. The layout is a plain single column on purpose: no table, text box or
  picture, so that the applicant-tracking systems read it correctly.
- **The `<number>`** changes with each generation: when an offer has several numbered folders, the
  up-to-date CV is in the one with the **highest number** (the old ones stay; you can delete
  them).
- **A CV for a single offer**: `jobpipe cv --job-id <id>`, with the identifier shown by
  `jobpipe offers`.
- **A CV for any offer you found yourself, in any field**: `jobpipe cv --url "<address of the
  offer>"` (see [section 7](#a-cv-for-any-offer-by-its-address)).
- **`note:`** gives information about the offer (for example that it asks for Dutch), not a
  problem with the CV.
- **`more offer(s) waiting`**: run `jobpipe cv` again to make the next CVs.

**"CV to check"**: the CV is still written, but jobpipe would rather you read it again before
sending it. The reason is shown under `why:`. The usual cases:

- *longer than one page*: shorten some bullets or set more of them to `droppable: true`;
- *a claim your profile does not back*: the sentence quoted under `why:` contains a number missing
  from `metrics`, a diploma not declared as obtained, a "we" or a word like "expert". Rephrase it
  in your profile.

**After changing your profile**, simply run `jobpipe cv` again (and again as long as
`more offer(s) waiting` is shown): the CVs are made again with the new version, in a new numbered
folder. `jobpipe cv --force` remakes all the CVs, even without changes.

---

## 6. Customise the search

Do not edit the files in the `config/` folder: they are replaced with each update. Put your
settings in a `local/` folder, which Git ignores. The `local.example/` folder contains commented
templates; copy the one you need:

```
mkdir local
copy local.example\companies.yaml local\      (Windows)
cp local.example/companies.yaml local/        (Mac, Linux)
```

Each file in `local/` adds to the file of the same name in `config/`: you only write in it what
you change.

**Add a company**: `local/companies.yaml`

```yaml
companies:
  - { slug: my-employer, name: My Employer, ats: greenhouse, ats_slug: myemployer }
```

`ats` is the service that hosts the company's careers page (`greenhouse`, `lever`, `ashby`,
`recruitee`, `smartrecruiters`, `workable`, `personio`) and `ats_slug` is the name that appears in
its address: `boards.greenhouse.io/<ats_slug>`, `jobs.lever.co/<ats_slug>`,
`jobs.ashbyhq.com/<ats_slug>`, `<ats_slug>.recruitee.com`... If you only know the address of the
careers page, jobpipe can recognise it:

```
jobpipe company add "My Employer" --careers-url https://...
jobpipe company resolve --slug my-employer
```

`jobpipe company list` shows the companies being followed.

**Change the search keywords or the accepted languages**: `local/taxonomy.yaml` (a copy of
`local.example/taxonomy.yaml`). Remove the `#` in front of the lines you want to turn on. A list
written there replaces the original list: copy it from `config/taxonomy.yaml` (the `queries`
section), then add or remove keywords. To accept offers in Dutch, set
`accepted: [fr, en, nl]`. To search a whole other field, see
[section 7](#search-another-field-automatically).

---

## 7. Use it for any kind of job

Out of the box, jobpipe looks for AI, data and machine-learning offers. For any other field
(sales, accounting, marketing, logistics, HR...) you have two ways, which you can combine: give
jobpipe the offers you found yourself, or change what `jobpipe run` looks for.

### A CV for any offer, by its address

You found an offer yourself (on a company site, a job board, Actiris...)? Open **the offer's own
page** (not a list of search results), copy its address from the browser's address bar, and run:

```
jobpipe cv --url "https://jobs.lever.co/kpler/be4d309c-8f01-48cc-98d4-f02cfc7b469f"
```

(with Docker: `docker compose run --rm jobpipe cv --url "https://..."`)

Keep the quotes around the address. jobpipe reads the offer, adds it to your list and writes the
CV, exactly as for the offers of `jobpipe run`. The filters (field, junior level, Brussels) are
skipped: you chose the offer. A real example, for a sales offer, with the fictional example
profile (without Docker, on Windows):

```
added: Account Development Representative | Kpler
reading the page of 1 offer(s) once, to learn the language and the skills asked...
CV ready: Account Development Representative | Kpler
    offer: https://jobs.lever.co/kpler/be4d309c-8f01-48cc-98d4-f02cfc7b469f
    PDF:   C:\Users\sam\jobpipe\documents\7b13fe081a7da281\15\outbox\Exemple_Sam_CV_Kpler.pdf
    Word:  C:\Users\sam\jobpipe\documents\7b13fe081a7da281\15\cv.docx  (editable copy)
1 CV(s) ready, 0 to check, 0 failed
```

- **Several offers at once**: repeat `--url`:
  `jobpipe cv --url "<address 1>" --url "<address 2>"`.
- **Add an offer now, make the CV later**: `jobpipe add "<address>"` (with Docker:
  `docker compose run --rm jobpipe add "<address>"`). It prints the offer's id; then
  `jobpipe cv --job-id <id>` makes the CV when your profile is ready.
- **`could not read a job posting at ...`**: the address is a list of offers, or a page that needs
  a login. Open the offer itself and copy that address. The offer pages of the job boards used by
  companies (Greenhouse, Lever, Ashby, Workable, Recruitee...) work best.

### Search another field automatically

`jobpipe run` decides what to look for with the file `config/taxonomy.yaml`. To look for another
field, create `local/taxonomy.yaml` (see [section 6](#6-customise-the-search) for the `local/`
folder): the lists you write there replace the AI/data lists. Start from the marketing example
shipped with jobpipe:

```
mkdir local                                                         (if it does not exist yet)
copy local.example\taxonomy-marketing.yaml local\taxonomy.yaml      (Windows)
cp local.example/taxonomy-marketing.yaml local/taxonomy.yaml        (Mac, Linux)
```

Open `local/taxonomy.yaml` in your editor. It contains:

```yaml
queries:
  en: [marketing, communication, social media, digital marketing, content]
  fr: [marketing, communication, marketing digital, réseaux sociaux, community manager]

domains:
  marketing:
    - marketing
    - digital marketing
    - marketing digital
    - communication
    - social media
    - réseaux sociaux
    - community manager
    - content
    - brand
    - seo
    - campaign
    - campagne
    - e-commerce

filters:
  domain:
    adjacent_terms: [copywriting, google analytics, hubspot, crm, events, événementiel]
    hard_exclude_title: ['\b(?:recruiter|talent acquisition)\b']
    soft_exclude_title: []

seniority:
  exclude_patterns:
    - '\bsenior\b'
    - '\bsr\.?\s'
    - '\blead\b'
    - '\bhead of\b'
    - '\bdirector\b'
    - '\b(?:[5-9]|1\d)\s*\+?\s*(?:years|yrs|ans|jaar)\b'
```

| Part | What it does | What you change |
|---|---|---|
| `queries` | The words jobpipe types in the search box of Actiris (and Adzuna), in English (`en`) and French (`fr`). | A few short, broad words of your field. Write both languages: a language you leave out keeps its AI/data searches. |
| `domains` | An offer is kept only if its **title** contains one of these words, or its **text** contains at least three of them. | The words of your field, in English and French. The group name (`marketing`) is free. |
| `filters` → `domain` | `adjacent_terms`: more words that count in the text. `hard_exclude_title` and `soft_exclude_title`: titles that are always dropped (the AI/data lists drop sales, marketing, HR and accounting titles). | Keep the two exclude lists empty (`[]`) unless a kind of job you do not want keeps coming. |
| `seniority` | Titles that mean "too senior for a junior". | The shipped list also drops every "manager"; the example keeps them, because junior "community manager" jobs exist. Leave this part out to keep the shipped list. |

The same file for **accounting** would start like this (keep the rest of the example):

```yaml
queries:
  en: [accountant, accounting, finance]
  fr: [comptable, comptabilité, aide-comptable, finance]

domains:
  accounting: [accountant, accounting, comptable, comptabilité, bookkeeping, finance, audit, tax, fiscalité]
```

Then run the search again and look at the offers kept:

```
jobpipe run --no-audit
jobpipe offers
```

(with Docker: `docker compose run --rm jobpipe run --no-audit`, then
`docker compose run --rm jobpipe offers`)

From now on, only the new searches run (the AI/data ones stop) and the new offers are judged with
your words. The offers kept earlier stay in the list. The area stays Brussels and the level stays
junior. To follow the employers of your field, add them in `local/companies.yaml`
([section 6](#6-customise-the-search)). To go back to AI and data, delete `local/taxonomy.yaml`.

### Tags for your field

The `tags` of your profile decide what comes first on the CV ([Your profile](#your-profile)), but
jobpipe only recognises the tags of its list, which are mostly technical. For another field, add
your own tags in `local/skills.yaml` (a copy of `local.example/skills.yaml`, without the `#`):

```yaml
skills:
  seo: [seo, search engine optimisation, search engine optimization, référencement]
  social_media: [social media, réseaux sociaux, instagram, linkedin, tiktok]
  copywriting: [copywriting, rédaction, content writing]
  google_analytics: [google analytics, ga4]
  crm: [crm, hubspot, salesforce]
  event_planning: [events, événementiel, évènementiel]
```

Each line is a tag, then the words an offer may use for it. Then use these tags in
`profile/profile.yaml`: a project tagged `[social_media, copywriting]` moves to the top for an
offer that asks for social media; add a `cv_titles` entry for your field (for example
"Marketing and communication", tagged `[social_media, seo]`) so that it is picked under your name.
Write your bullets with the words of your field too: the CV only uses sentences from your profile.

Set up your tags **before** making the CVs of these offers: jobpipe reads the skills an offer asks
for once, the first time it makes its CV.

---

## 8. Update jobpipe

From the `jobpipe` folder:

```
git pull
```

then:

- with Docker: `docker compose build`
- without Docker (environment activated): `pip install -e .`

Your personal files (`.env`, `profile/profile.yaml`, `local/`, `data/`, `documents/`) are not
tracked by Git: `git pull` does not touch them and does not create conflicts, as long as you have
not changed the project files themselves (see the FAQ if `git pull` refuses).

---

## 9. Common problems

**`jobpipe` is not recognised** (without Docker): the environment is not activated. Go to the
`jobpipe` folder and run `.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate`
(Mac, Linux).

**`py` or `python3` is not recognised**: Python is not installed, or not in the PATH.
Reinstall it from python.org and tick "Add python.exe to PATH", then open a new terminal.

**`failed to connect to the docker API` or `Cannot connect to the Docker daemon`**: Docker Desktop
is not running. Open it, wait for "Engine running", then run the command again.

**`configuration directory not found`**: you are not in the `jobpipe` folder. Run `cd jobpipe`
(or the full path of the folder) and try again.

**A message about `contact`**: the contact address is missing. Run
`jobpipe init --contact your.address@example.org`.

**`Profile error`**: the message gives the line to fix (for example `projects[2].bullets[1].en`
= the 1st bullet of the 2nd project, English sentence) and the expected format. Compare with
`profile/profile.example.yaml`.

**`no offer kept yet`**: first run `jobpipe run --no-audit`, then `jobpipe cv`.

**No new offer kept**: this is normal on some days. Try again the next day, add an Adzuna key, or
add companies and keywords (section 6).

**The CV does not update**: close the PDF if it is open in a viewer (Windows locks an open file),
then run `jobpipe cv --force`.

**`git pull` refuses because of local changes**: you changed a project file (often in
`config/`). `git status` shows which one. Copy your changes into `local/` (section 6), then undo
the change with `git checkout -- <file>` and run `git pull` again.

**Linux with Docker: the files created belong to `root`**: add
`--user "$(id -u):$(id -g)"` after `run`, for example
`docker compose run --rm --user "$(id -u):$(id -g)" jobpipe cv`.

**The `actiris` line reports an error**: the Actiris site was not responding. The other sources
were still read; run `jobpipe run --no-audit` again later. If the error lasts several days and
suggests installing a browser (without Docker only): `pip install -e ".[browser]"`,
`playwright install chromium`, then `jobpipe actiris-discover`.
