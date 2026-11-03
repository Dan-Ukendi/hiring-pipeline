# jobpipe — Plan d'architecture de la phase 3 : adaptation CV + lettre de motivation

Document de conception. Aucun code source n'est livré ici. Ce plan est destiné à être converti en prompt d'implémentation.

Portée : à partir d'une offre (phase 1) et de son plan de candidature (phase 2), produire les documents prêts à soumettre et les réponses aux questions de l'employeur, avec une garantie structurelle de non-fabrication.

---

## 0. Décision d'architecture fondatrice

La tentation naturelle est : « un prompt, le CV de Dan en contexte, l'offre en contexte, génère un CV adapté ». Cette approche est indéfendable sur la contrainte centrale. Un modèle 8B qui reformule librement un parcours **inventera** : il transformera « plusieurs années dans l'horeca » en « 5 ans de management d'équipe », « bootcamp BeCode » en « formation certifiante en data science », « études d'ingénieur arrêtées en BA3 » en « formation d'ingénieur civil à l'ULB ». Ce ne sont pas des hallucinations exotiques, c'est le comportement par défaut d'un modèle de génération de CV.

La décision est donc de **retirer au LLM la capacité d'écrire le CV**.

Le pipeline se scinde en trois étages :

| Étage | Qui écrit | Sortie | Risque de fabrication |
|---|---|---|---|
| A — `plan` | LLM, sortie JSON contrainte | une liste d'identifiants de faits + un ordre | nul : tout identifiant inconnu est rejeté |
| B — `prose` | LLM, texte libre borné | le corps de la lettre, la ligne de résumé du CV, les réponses ouvertes | réel, et c'est là que le garde-fou travaille |
| C — `render` | code déterministe | `.docx`, `.pdf`, `.txt` | nul |

Le CV adapté est produit à 95 % par l'étage C : c'est une **sélection et un ordonnancement** de phrases que Dan a écrites lui-même. Le LLM choisit quoi montrer, pas quoi dire. Seuls 2 à 3 lignes de résumé en tête du CV sont du texte généré, et elles passent par le garde-fou.

La lettre de motivation, elle, est forcément du texte généré. C'est le point faible assumé de la phase, traité en section 11.

---

## 1. Source de vérité du profil

### 1.1 Format

Un seul fichier `profile/profile.yaml`, versionné dans le dépôt, **écrit à la main par Dan, jamais écrit par le pipeline**. YAML et non JSON : Dan doit pouvoir le relire et le corriger sans friction, et les blocs de texte multi-lignes y sont lisibles. YAML et non SQLite : c'est un document d'auteur, il appartient à git, il doit se differ.

Un second fichier `profile/declarations.yaml` isole les données que Dan déclare une fois pour toutes et qui ne doivent jamais être déduites (éligibilité au travail, prétentions, préavis, permis). Séparé parce que ces champs ont une sémantique juridique et ne doivent pas se noyer dans la narration.

Chargement : `Profile.load()` → validation Pydantic → calcul d'un `content_hash` (SHA-256 du YAML canonicalisé) → ce hash est stocké sur chaque run. Tout artefact est donc traçable à l'état exact du profil qui l'a produit.

### 1.2 Principe du schéma : le fait atomique et la banque de formulations

Deux idées portent tout le reste.

**Le fait atomique.** Toute affirmation factuelle du profil est un enregistrement avec un `fact_id` stable. Un `fact_id` est une chaîne lisible (`exp.care.role`, `proj.innovation_radar.metric.vertical_axes`, `edu.ulb.level`). Un fait porte une valeur typée, pas une phrase.

**La banque de formulations.** Pour chaque chose que Dan peut dire de lui, le profil contient des variantes **pré-rédigées par Dan**, une par langue et par registre. Le LLM ne traduit pas et ne reformule pas un fait : il sélectionne une variante par son identifiant. C'est ce qui rend l'anti-fabrication vérifiable — l'ensemble des phrases factuelles possibles dans un CV est fini, connu, et relu par un humain une seule fois.

Coût honnête de ce choix : Dan doit écrire, une fois, environ 60 à 90 puces en français et en anglais. C'est une à deux soirées de travail. C'est le prix de la garantie, et c'est un investissement qui sert aussi ses candidatures manuelles et son LinkedIn.

### 1.3 Schéma

```yaml
schema_version: 1

identity:
  given_name: Dan
  family_name: Ukendi
  city: Bruxelles
  country_code: BE
  email: ...
  phone_e164: ...
  links:
    - { kind: linkedin, url: ..., show_in: [cv] }
    - { kind: github,   url: ..., show_in: [cv] }
  # No photo, no birth date, no nationality, no marital status: Belgian convention.

languages:
  - code: fr
    cefr: { comprehension: C2, speaking: C2, writing: C2 }
    is_native: true
  - code: en
    cefr: { comprehension: C1, speaking: C1, writing: C1 }
  - code: nl
    cefr: { comprehension: B2, speaking: A2, writing: A2 }
    can_author_documents: false        # hard switch read by the language router

education:
  - entry_id: edu.ulb
    institution: Université libre de Bruxelles
    programme: Ingénieur civil
    level: bachelor
    completed: false
    credential: null                   # must stay null; never rendered as a degree
    start_year: ...
    end_year: ...
    interruption_stage: BA3
    disclosure_policy: state_without_credential
    phrasings:
      fr: "Université libre de Bruxelles — cursus d'ingénieur civil (polytechnique), interrompu en BA3"
      en: "Université libre de Bruxelles — civil engineering programme (polytechnique), discontinued in third year"
  - entry_id: edu.becode
    institution: BeCode
    programme: AI & Data — spécialisation GenAI
    level: bootcamp
    completed: false
    in_progress_until: 2026-12
    credential: null
    phrasings: { fr: ..., en: ... }

experience:
  - entry_id: exp.care
    organisation: CaRe
    role_fr: Co-fondateur et CEO
    role_en: Co-founder and CEO
    sector: saas_horeca
    start: 2024-xx                     # Dan fills exact months
    end: null
    is_current: true
    context_phrasings:
      fr: "SaaS pour cafés et restaurants en Belgique"
      en: "SaaS platform for Belgian cafés and restaurants"
    bullets:
      - bullet_id: exp.care.b1
        depends_on: [exp.care.role, exp.care.org]
        tags: [product, stakeholders, business]
        droppable: false
        fr: "..."
        en: "..."
  - entry_id: exp.horeca
    organisation_kind: multiple_employers   # not a single org; never rendered as one
    role_fr: Postes opérationnels en horeca
    role_en: Operational roles in hospitality
    start: ...
    end: ...
    bullets: [...]

projects:
  - entry_id: proj.innovation_radar
    name: Innovation Radar
    client: Orange Business
    engagement_kind: client_brief_delivered_to_production
    role_fr: ...
    stack: [python, ...]
    tags: [genai, classification, pipeline, client]
    metrics: [metric.ir.axes]
    bullets: [...]
  - entry_id: proj.ing_poc
    name: ...
    client: ING
    engagement_kind: team_proof_of_concept
    team_size: ...
    metrics: [metric.ing.tests, metric.ing.ci]
    bullets: [...]
  - entry_id: proj.rag_eval
    name: ...
    engagement_kind: personal
    metrics: [metric.rag.questions, metric.rag.judge]
    bullets: [...]

metrics:
  - metric_id: metric.ing.tests
    value: 449
    unit: tests
    scope: "team-built proof of concept"
    verified_by: repository
    phrasings:
      fr: "449 tests automatisés et une intégration continue"
      en: "449 automated tests with continuous integration"
  - metric_id: metric.rag.questions
    value: 40
    unit: questions
    scope: "hand-verified evaluation set"
    phrasings: { fr: ..., en: ... }

skills:
  - skill_id: skill.python
    label: Python
    category: language
    evidence_refs: [proj.innovation_radar, proj.ing_poc, proj.rag_eval]
    first_used_year: ...
    # No self-declared level. Evidence or nothing.
  - skill_id: skill.rag
    label: RAG
    category: genai
    evidence_refs: [proj.rag_eval]

claims_bank:
  - claim_id: claim.eval_first
    depends_on: [proj.rag_eval, metric.rag.questions]
    kind: positioning
    fr: "J'écris le harnais d'évaluation avant le code : ..."
    en: "I write the evaluation harness before the code: ..."
  - claim_id: claim.founder_angle
    depends_on: [exp.care, exp.horeca]
    kind: positioning
    fr: "..."
    en: "..."
  - claim_id: claim.degree_context
    depends_on: [edu.ulb, exp.horeca, edu.becode]
    kind: sensitive
    requires_human_review: true
    fr: "..."

never_claim:
  - { pattern_kind: degree,        note: "no completed higher-education credential" }
  - { pattern_kind: certification, note: "AWS Cloud Practitioner started, not completed" }
  - { pattern_kind: years_of_professional_software_experience }
  - { pattern_kind: employment_at_orange_business, note: "client brief, not employee" }
  - { pattern_kind: employment_at_ing,             note: "proof of concept, not employee" }
  - { pattern_kind: dutch_authoring }
```

Points de conception à défendre :

- `completed: false` et `credential: null` sur `edu.ulb` ne sont pas décoratifs : le renderer refuse d'émettre une ligne de diplôme pour une entrée dont `credential` est nul, et le garde-fou L3 déclenche sur tout mot de diplôme en sortie. L'impossibilité de revendiquer un diplôme est câblée en deux endroits indépendants.
- `skills[].evidence_refs` sans niveau auto-déclaré : une compétence n'entre dans le CV que si elle est adossée à un projet réel. Cela supprime mécaniquement le bourrage de mots-clés (section 3.4).
- `exp.horeca.organisation_kind: multiple_employers` : sans ce champ, un modèle produit « Restaurant X, 2019–2023 » en inventant un nom d'employeur. C'est le genre de fabrication la plus coûteuse et la plus facile à vérifier par un recruteur.
- `never_claim` est consommé par le garde-fou, pas seulement documentaire.

---

## 2. Vue d'ensemble du flux

```
OfferRecord (phase 1) ─┐
ApplicationPlan (ph.2) ─┼→ build_offer_context()  → OfferContext
Profile + Declarations ─┘
                           ↓
                   route_language()                 → DocumentLanguage  (déterministe)
                           ↓
                   stage_a_select()   [LLM, JSON]   → SelectionPlan     (fact_ids only)
                           ↓
                   validate_plan()                  → rejet si fact_id inconnu
                           ↓
            ┌──────────────┼───────────────┐
            ↓              ↓               ↓
    build_cv_doc()   stage_b_letter()  answer_questions()
    (déterministe)   [LLM, prose]      (routeur: déterministe | LLM | hold)
            ↓              ↓               ↓
            └──────────────┼───────────────┘
                           ↓
                   guard_run()   L0→L4              → GuardReport
                           ↓
                 status = clean | held
                           ↓
                   render()  docx → pdf             → RenderedDocument[]
                           ↓
                   round_trip_check()               → parse order + page count
                           ↓
                   ApplicationPackage               → phase 4
```

Un seul appel LLM peut échouer sans perdre le run : chaque étage persiste son artefact, et `jobpipe generate --offer <id> --resume` reprend à l'étage manquant. Nécessaire sur ce matériel, où un run de 20 offres dure des dizaines de minutes.

---

## 3. Génération du CV adapté

### 3.1 Ce qui est fixe, ce qui bouge

| Fixe entre toutes les offres | Adapté par offre |
|---|---|
| identité, coordonnées, liens | ligne de résumé (2–3 lignes, LLM) |
| libellés des postes et des organisations | titre du CV (sélectionné parmi une liste finie de titres pré-écrits) |
| dates | ordre des blocs `projects` |
| texte de chaque puce (mot pour mot) | sous-ensemble de puces retenues par entrée |
| intitulés de rubriques | sous-ensemble et ordre des `skills` |
| rubrique langues (CECR) | ordre interne de la rubrique compétences |
| politique de divulgation de `edu.ulb` | inclusion ou non des entrées `droppable` |

Le texte d'une puce ne change jamais. C'est contre-intuitif pour quiconque a l'habitude de « réécrire son CV pour l'offre », et c'est précisément ce qui rend la garantie vérifiable. L'adaptation réelle qui compte pour un ATS et pour un lecteur humain est la **sélection et la hiérarchie**, pas la paraphrase.

### 3.2 Titre du CV

Le profil contient `cv_titles[]`, 6 à 10 titres pré-écrits par Dan (`AI / GenAI Engineer (junior)`, `Data Engineer — junior`, `Ingénieur data — profil junior`, …), chacun avec ses langues. L'étage A choisit un `title_id`. Le LLM n'écrit pas le titre. Raison : le titre est le champ le plus lu et le plus parsé, et c'est aussi celui où un modèle glisse le plus volontiers un « Senior » ou un « Certified ».

### 3.3 Sélection et ordonnancement

L'étage A produit un `SelectionPlan`. Le scoring est hybride, et c'est volontaire :

1. **Pré-score déterministe** (hors LLM, testable sans Ollama) : pour chaque `project`/`experience`/`skill`, un score de pertinence = recouvrement entre ses `tags`/`evidence_refs` et les compétences requises extraites par la phase 2, pondéré par la récence et par `engagement_kind` (un brief client livré pèse plus qu'un projet perso).
2. **Réordonnancement LLM** : le modèle reçoit le pré-score et peut permuter, justifier en une ligne par élément, et retirer des éléments `droppable`. Il ne peut pas en ajouter.

Le pré-score seul donne déjà un CV correct ; c'est le mode `--no-llm-plan`, qui sert de référence dans l'évaluation. Si l'étage A ne bat pas le pré-score déterministe sur les métriques de la section 9, il doit être retiré. Cette question doit être tranchée par les chiffres, pas par principe.

### 3.4 Mots-clés sans bourrage

Le bourrage de mots-clés est structurellement impossible ici, parce que les mots-clés ne sont pas insérés : ils sont **sélectionnés dans un ensemble fermé**. Une compétence absente de `profile.skills` ne peut pas apparaître dans le CV, quelle que soit son importance dans l'offre.

Ce que fait quand même l'étage A, utile et sûr :
- **Alignement lexical** : si l'offre dit « LLMOps » et que `skill.rag` porte l'alias `LLMOps` dans `aliases[]` (alias écrits par Dan, pas inventés), c'est l'alias qui est rendu. Les alias sont une liste fermée, relue par Dan.
- **Rapport de manques** : les compétences requises par l'offre et absentes du profil sont écrites dans `selection_plans.gap_json`. Ce n'est pas mis dans le CV. C'est remonté à Dan, et c'est accessoirement la meilleure donnée d'orientation d'apprentissage que le pipeline produira — bien plus utile que les lettres.

La métrique de l'évaluation est double et doit l'être : `keyword_coverage` (part des compétences requises *que Dan possède réellement* et qui apparaissent dans le document) et `unsupported_keyword_rate` (compétences dans le document absentes du profil), qui doit valoir exactement 0.

### 3.5 Longueur

Cible : **une page**, non négociable pour un profil junior en Belgique.

Boucle déterministe, sans LLM :
1. rendu `.docx`
2. conversion PDF
3. lecture du nombre de pages réel (`pypdf`)
4. si > 1 : retirer la puce `droppable` de plus faible score dans le `SelectionPlan`, re-rendre, recommencer
5. plafond de 12 itérations ; au-delà, `status = held` avec `hold_reason = layout_overflow`

Le comptage de pages est mesuré sur le fichier produit, pas estimé. Un estimateur de caractères se trompe dès que les puces sont inégales.

---

## 4. Lettre de motivation

### 4.1 Structure imposée

Quatre blocs, émis dans cet ordre, chacun avec un budget de mots. La structure est du code, pas une consigne de prompt : le LLM remplit des blocs séparément ou en un passage dont le découpage est vérifié.

| Bloc | Budget | Contenu | Source de la spécificité |
|---|---|---|---|
| `hook` | 35–55 mots | poste visé, canal, un élément précis de l'offre | obligatoirement un `offer_evidence_span` (offset dans le texte de l'offre) |
| `proof` | 90–140 mots | 2 projets du `SelectionPlan`, métriques de `metrics` | `fact_ids` uniquement |
| `fit` | 50–90 mots | pourquoi cet employeur | `employer_facts` de la phase 2 ; **bloc omis si cette liste est vide** |
| `close` | 30–45 mots | disponibilité, formule | `declarations` |

Total 205–330 mots, hors en-tête et formule. En dessous de 200 mots une lettre paraît expédiée ; au-delà de 350 elle n'est pas lue.

Le bloc `fit` est le cœur du problème. Une lettre « spécifique » l'est parce qu'elle dit quelque chose que seul quelqu'un ayant regardé l'employeur pourrait dire. Le pipeline ne dispose que du texte de l'offre et de ce que la phase 2 a pu capturer. **Donc : si la phase 2 ne remonte aucun fait employeur exploitable, le bloc est supprimé plutôt que rempli de généralités.** Une lettre de trois blocs honnête bat une lettre de quatre blocs dont le quatrième dit « votre entreprise innovante dans un secteur en pleine évolution ». C'est la décision de conception la plus importante de cette section.

### 4.2 Registre et conventions belges

- Vouvoiement systématique en français.
- Adresse : `Madame, Monsieur,` si aucun contact nommé ; `Madame <Nom>,` / `Monsieur <Nom>,` si la phase 2 en remonte un. Pas de `Chère équipe de recrutement`, pas de `À qui de droit`.
- Clôture française de Belgique, registre sobre : `Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.` La variante française « sentiments respectueux/dévoués » sonne plus guindée qu'attendu en Belgique ; `salutations distinguées` est le registre neutre correct. (Confiance moyenne : pratique courante, pas une norme écrite que j'ai pu sourcer.)
- Clôture anglaise : `Yours sincerely,` avec nom nommé, `Yours faithfully,` sans. Pas de `Best,`.
- Première personne du singulier exclusivement. `nous` et `on` sont interdits et détectés par un lint bloquant (L3). Cela règle aussi le cas piégeux où le modèle parle de CaRe en « nous » et laisse croire à un emploi salarié.
- Pas de photo, pas de date de naissance, pas de nationalité, pas d'état civil, ni dans le CV ni dans la lettre. **Vérifié** : les sources belges consultées sont explicites là-dessus (voir section 13).
- Pas de mention de prétentions salariales dans la lettre, jamais, sauf si la phase 2 remonte que l'offre l'exige explicitement.
- Pas d'excuse ni de justification du parcours interrompu. Le parcours est énoncé en positif via `claim.founder_angle`, ou il n'est pas évoqué du tout.

### 4.3 Gabarits de prompt

Rédigés en anglais, versionnés sous `prompts/phase3/`, chaque fichier portant un `prompt_version` repris dans la table `generation_runs`. Changer un prompt sans incrémenter la version casse la comparabilité de l'évaluation : le chargeur refuse un prompt dont le hash ne correspond pas à la version déclarée.

`prompts/phase3/select.v1.md` (étage A) :

```
You are a selection engine for a job application. You do not write prose.

CANDIDATE FACTS (closed set — these are the only things that exist):
{{facts_digest}}

OFFER:
title: {{offer.title}}
employer: {{offer.employer}}
required_skills: {{offer.required_skills}}
body_excerpt: {{offer.body_excerpt}}

PRECOMPUTED RELEVANCE SCORES:
{{prescores}}

Return a single JSON object matching this schema, and nothing else:
{
  "title_id": "<one id from cv_titles>",
  "project_order": ["<entry_id>", ...],
  "experience_order": ["<entry_id>", ...],
  "dropped_bullet_ids": ["<bullet_id>", ...],
  "skill_order": ["<skill_id>", ...],
  "letter_proof_entry_ids": ["<entry_id>", "<entry_id>"],
  "letter_claim_ids": ["<claim_id>", ...],
  "gap_skills": ["<skill label from the offer, verbatim>", ...],
  "rationale": {"<entry_id>": "<max 15 words>"}
}

Rules:
- Every id you emit must appear in CANDIDATE FACTS. Emitting an id that does not
  appear there is an error and the output will be discarded.
- Do not invent ids. Do not rename ids. Do not write candidate-facing text.
- gap_skills lists offer requirements that CANDIDATE FACTS does not support.
```

`prompts/phase3/letter.v1.md` (étage B) :

```
Write the body of a job application letter in {{language_name}}.

You may state ONLY what the SELECTED FACTS below contain. You may not add any
organisation, person, place, product, technology, number, date, duration,
degree, certification, or job title that does not appear there. If a fact you
would like to use is absent, write the letter without it.

SELECTED FACTS:
{{selected_facts_rendered}}

EMPLOYER FACTS (may be empty):
{{employer_facts}}

OFFER TITLE: {{offer.title}}
OFFER EVIDENCE YOU MUST REFER TO (quote or paraphrase one): {{offer_evidence_spans}}

Write exactly these blocks, separated by a blank line, in this order:
1. hook — {{hook_min}}–{{hook_max}} words. Name the role. Refer to one concrete
   element of OFFER EVIDENCE.
2. proof — {{proof_min}}–{{proof_max}} words. Use the two selected work items.
   Use their numbers exactly as written in SELECTED FACTS.
3. fit — {{fit_min}}–{{fit_max}} words. Use EMPLOYER FACTS only.
   If EMPLOYER FACTS is empty, omit this block entirely and write nothing in its place.
4. close — {{close_min}}–{{close_max}} words. State availability. No salary.

Constraints:
- First person singular throughout. Never "we", never "on", never "nous".
- No salutation, no sign-off, no address block, no subject line: those are added
  by the renderer.
- No self-assessed skill levels. No adjectives about your own abilities that the
  facts do not support.
- Do not claim a completed degree or certification.
- Plain professional register. No em dashes. No rhetorical questions.
- Banned openings and phrases: {{banned_phrases}}
Output the blocks only.
```

`prompts/phase3/judge.v1.md` (L4, voir 6.5) et `prompts/phase3/answer.v1.md` (questions ouvertes) suivent la même discipline : jeu de faits fermé, interdiction explicite d'ajout, sortie strictement formatée.

---

## 5. Langue du document

### 5.1 Routeur déterministe

`route_language(offer, application_plan, profile) -> DocumentLanguage`, pur, testable hors-ligne, décidé dans cet ordre :

1. Si la phase 2 remonte une langue de candidature explicitement imposée → celle-là, sous réserve de la règle 5 ci-dessous.
2. Si l'offre liste une exigence linguistique dure (`Nederlands vereist`, `Dutch is a must`) → voir 5.2.
3. Détection de langue sur le corps de l'offre, avec un détecteur embarqué et épinglé (`lingua-py` ou `py3langid`, aucun appel réseau), seuil de confiance 0,80.
4. Si confiance < 0,80 ou texte mixte (fréquent à Bruxelles) → **anglais**.
5. Garde-fou final : si la langue retenue a `can_author_documents: false` dans le profil → bascule vers l'anglais et journalisation d'un `language_downgrade`.

### 5.2 Le cas néerlandais, décidé sans détour

Dan a B2 en compréhension et son profil déclare A2 en production. **Le pipeline ne génère pas de documents en néerlandais.** Raisons, dans l'ordre d'importance :

1. Une lettre néerlandaise produite par un modèle 8B et non relue par quelqu'un qui écrit le néerlandais sera immédiatement identifiée comme machine par un recruteur néerlandophone. Le coût n'est pas « lettre moyenne », c'est « candidat qui a menti sur son niveau ».
2. Envoyer une lettre néerlandaise parfaite alors que la production est A2 crée une attente que l'entretien détruira.
3. L'anglais est accepté à Bruxelles et la source consultée recommande explicitement l'anglais en cas de doute (**vérifié**, section 13).

Comportement pour une offre néerlandophone :
- Documents en anglais.
- La rubrique langues indique le néerlandais à son niveau réel par modalité : `Nederlands — B2 (begrijpen) / A2 (spreken, schrijven)`. Déclarer la ventilation par modalité est honnête et désarme la question.
- Une phrase de la banque, `claim.nl_comprehension`, pré-écrite par Dan, peut entrer dans le bloc `close` : il suit le néerlandais à l'écrit et à l'oral passif, il est disponible pour progresser.
- Si l'offre pose le néerlandais comme exigence dure : `status = held`, `hold_reason = nl_required`. Pas d'envoi automatique. Dan tranche.

Drapeau `--allow-nl-generation` existant mais désactivé par défaut, uniquement pour que Dan puisse mesurer la qualité réelle s'il le souhaite.

Conséquence assumée : une part des offres flamandes et bruxelloises néerlandophones sortira du périmètre automatique. C'est le bon arbitrage.

---

## 6. Questions personnalisées et garde-fou anti-fabrication

### 6.1 Taxonomie et politique

Chaque `CustomQuestion` remontée par la phase 2 est classée par un classifieur **déterministe d'abord** (règles sur mots-clés et sur le type de champ remonté : `select`, `boolean`, `number`, `text`), avec repli LLM uniquement pour départager, et `unknown` par défaut. Politique par catégorie :

| Catégorie | Source de la réponse | LLM | Envoi auto |
|---|---|---|---|
| `work_authorization` | `declarations.work_authorization` | non | oui |
| `sponsorship_needed` | `declarations.sponsorship_needed` | non | oui |
| `availability` / `notice_period` | `declarations` | non | oui |
| `driving_licence`, `relocation`, `remote_preference` | `declarations` | non | oui |
| `years_of_experience_with_X` | calcul déterministe sur les dates du profil | non | oui, voir 6.2 |
| `education_level` (champ fermé) | `declarations.highest_completed_level` | non | oui |
| `salary_expectation`, champ numérique obligatoire | `declarations.salary_expectation` | non | oui |
| `salary_expectation`, champ texte libre | — | non | **non — `held`** |
| `motivation_open`, `why_us`, `describe_a_project` | banque de faits + étage B | oui | oui si garde-fou propre |
| `degree_requirement_hard_filter` | — | non | **non — `held`** |
| `reference_contact_details` | — | non | **non — `held`** |
| `protected_characteristic` (âge, origine, santé, situation familiale, convictions) | — | non | **non — `held`** |
| `unknown` | — | non | **non — `held`** |

`declarations.yaml` est rempli une fois par Dan. Rien dans cette table n'est déduit du contexte : l'éligibilité au travail et le niveau d'études sont des déclarations juridiquement engageantes et le pipeline n'a pas à les inférer.

### 6.2 Répondre honnêtement quand la vérité désavantage

C'est la vraie question posée. La réponse de conception est : **la valeur numérique n'est jamais ajustée, le cadrage l'est.**

- Champ numérique fermé (« années d'expérience en Python : _ ») → la valeur calculée, sans arrondi vers le haut. Le calcul est explicite et vérifiable : `professional_years(skill)` ne compte que les périodes où la compétence est adossée à une entrée `experience` ou à un `engagement_kind` client. Un projet de bootcamp ne compte pas comme expérience professionnelle. Si le résultat honnête est 0 ou 1, c'est 0 ou 1.
- Champ texte où un nombre est demandé → la valeur, puis **une** phrase de contexte issue de la banque (`claim.experience_context`), pré-écrite par Dan. Le LLM ne rédige pas ce cadrage.
- Filtre éliminatoire dur (« minimum 3 ans ») → configurable : `skip` (ne pas candidater) ou `held` (Dan décide). Défaut `held` pour les offres de tier A, `skip` sinon. Mentir ici est à la fois une fraude et une perte de temps : l'écart se voit en 8 minutes d'entretien.
- Question sur l'absence de diplôme → **toujours** `held`. Formuler un parcours interrompu est un acte de jugement sur sa propre trajectoire. Un modèle 8B produira soit une excuse, soit une tournure qui frôle le mensonge. Dan écrit cette réponse une fois, elle entre dans la banque avec `requires_human_review: true`, et seules les offres où la formulation stockée s'applique telle quelle passent en automatique.

### 6.3 Arrêt systématique

Au-delà du tableau : tout champ dont la réponse générée déclenche une violation bloquante, tout champ obligatoire dont la catégorie est `unknown`, et tout champ dont la limite de caractères remontée par la phase 2 est inférieure au minimum du bloc correspondant.

### 6.4 Le garde-fou — conception

Cinq couches, dans l'ordre, chacune une fonction pure `(text, profile, offer_context) -> list[Violation]`. Aucun réseau, aucun LLM sauf L4. Chaque `Violation` porte `layer`, `code`, `severity ∈ {blocking, warning, info}`, `span_start`, `span_end`, `excerpt`, `detail`.

**L0 — Schéma et résolution d'identifiants.** Tout `fact_id`, `bullet_id`, `claim_id`, `metric_id`, `title_id` du `SelectionPlan` doit résoudre dans le profil chargé. Un identifiant inconnu invalide le plan : une tentative de relance, puis `held`. Code `UNKNOWN_FACT_ID`. Cette couche à elle seule met le CV hors d'atteinte de la fabrication.

**L1 — Provenance lexicale des entités.** Construction d'un `allow_set` d'entités autorisées :
- tous les noms propres du profil (organisations, institutions, clients, noms de projets, technologies, villes) ;
- les entités de l'offre : nom de l'employeur, titre du poste, produits et technologies cités, extraits mot pour mot du texte de l'offre ;
- une liste close de mots fonctionnels et de termes génériques du domaine (`lexicons/common.<lang>.txt`), relue par Dan.

Extraction depuis le texte généré : séquences de tokens capitalisés hors début de phrase, tokens d'un lexique technologique curé, acronymes. Toute entité extraite hors de l'`allow_set` → `UNSOURCED_ENTITY`, **bloquant**. C'est la couche qui attrape « Accenture », « Docker », « Université de Gand » apparus de nulle part.

**L2 — Provenance numérique.** Tout numéral et toute expression de quantité du texte doit se retrouver dans un `number_allow_set` = valeurs des `metrics` + années des dates du profil + nombres présents mot pour mot dans l'offre. Normalisation obligatoire avant comparaison :
- chiffres et nombres écrits en lettres, par langue (`quatre ans`, `four years`, `vier jaar`) ;
- pourcentages, multiplicateurs (`×3`, `threefold`), ordres de grandeur (`des centaines de`) ;
- durées dérivées (« 4 ans d'expérience » doit correspondre à un calcul de dates du profil, pas seulement à un nombre présent quelque part).
Hors ensemble → `UNSOURCED_NUMBER`, **bloquant**. Les chiffres sont la fabrication la plus dommageable et la plus facile à détecter mécaniquement : cette couche doit être la plus soignée du projet.

**L3 — Détecteurs de formes de revendication.** Expressions régulières par langue, dérivées de `never_claim` :
- diplôme : `diplômé|licencié|master|bachelier|ingénieur civil (?!.*interrompu)|graduated|degree in|gediplomeerd` ;
- certification : `certifié|certification|certified|AWS Certified` ;
- années d'expérience non adossées : `\d+\s*(ans|années|years|jaar)\s+(d'|of )?(expérience|experience|ervaring)` renvoyé vers L2 pour vérification du calcul ;
- emploi fictif : nom d'un client (`Orange Business`, `ING`) à proximité de `chez|at|employé|employee|poste chez` ;
- première personne du pluriel : `\b(nous|on)\b` et `\bwe\b` ;
- superlatifs auto-attribués non adossés : `expert|maîtrise parfaite|extensive experience` ;
- banque de formules interdites (section 11.2).
Sévérité `blocking` pour diplôme, certification, emploi fictif et pluriel ; `warning` pour les superlatifs.

**L4 — Vérification d'étayage par juge LLM.** Découpage du texte en phrases, puis pour chaque phrase un appel au modèle juge avec le jeu de faits sélectionné, sortie `supported | opinion | unsupported`. `opinion` est admis (une phrase de motivation n'est pas étayable factuellement). `unsupported` → `warning`, pas `blocking`.

Pourquoi `warning` et pas `blocking` : un juge de 8 B sur ce matériel n'est pas assez fiable pour bloquer. Il produira des faux positifs sur des formulations légitimes et des faux négatifs sur des fabrications plausibles. L4 est un filet **secondaire** qui sert surtout à alimenter le jeu de cas adverses : chaque `unsupported` que Dan confirme devient un cas de test de L1/L2/L3. C'est la boucle qui fait monter la garantie avec le temps. Prétendre l'inverse serait se mentir sur la solidité du dispositif.

**L5 — Portillon avant soumission.** `GuardReport.has_blocking` → `ApplicationPackage.status = held`. La phase 4 refuse tout paquet dont le statut n'est pas `approved`. Le refus est du côté phase 4 autant que phase 3 : une double négation, pour qu'un bug de la phase 3 ne suffise pas à envoyer un document non vérifié.

### 6.5 Rendre la garantie testable

C'est la partie que je considère comme non-négociable dans l'implémentation.

1. **Corpus adverse.** `tests/fixtures/adversarial/` — 40 à 60 fichiers, chacun un texte de lettre ou de puce de CV **écrit à la main** et contenant une fabrication précise, accompagné d'un `.expected.json` listant les codes de violation attendus et leurs offsets. Couverture obligatoire : employeur inventé, diplôme revendiqué, nombre gonflé, nombre écrit en lettres, technologie absente du profil, client transformé en employeur, certification, « nous », durée incohérente avec les dates, entité en néerlandais, entité correcte mais au mauvais endroit. Test de régression classique, zéro dépendance.
2. **Test de non-régression inverse.** `tests/fixtures/clean/` — textes légitimes qui ne doivent produire aucune violation bloquante. Sans ce pendant, le garde-fou dérive vers « tout bloquer », ce qui est une autre façon d'être inutilisable.
3. **Test de propriété.** Sur tout document du corpus d'évaluation : tout numéral du document apparaît dans le `number_allow_set`, et toute entité capitalisée apparaît dans l'`allow_set`. Exprimé comme propriété et non comme cas, avec Hypothesis sur des profils et des offres synthétiques.
4. **Test de mutation du profil.** Retirer une `metric` du profil doit faire basculer en violation un document qui la citait. Vérifie que le garde-fou lit réellement le profil et ne contient pas une liste codée en dur.
5. **Client LLM enregistré/rejoué.** `LLMClient` a deux implémentations : `OllamaClient` et `CassetteClient`, qui lit des réponses figées dans `eval/cassettes/<case_id>/<stage>.json`. Toute la chaîne — plan, lettre, garde-fou, rendu, round-trip — tourne en CI sans réseau et sans Ollama. C'est ce qui satisfait la contrainte « testable hors-ligne » sans se limiter à tester les fonctions pures.

---

## 7. Rendu de fichier

### 7.1 Formats et faits vérifiés

**Vérifié** (documentation Greenhouse, table de restrictions d'attachements inter-ATS, section 13) :
- Greenhouse accepte `.doc`, `.docx`, `.pdf`, `.rtf`, `.txt` ; limite 100 Mo.
- Workday accepte `.doc`, `.docx`, `.pdf`, `.txt` (et des formats non pertinents ici) ; limite 25 Mo par fichier.
- SmartRecruiters 10 Mo par fichier, Softgarden 5 Mo, Personio 20 Mo, Recruitee et Dvinci acceptent `.doc/.docx/.pdf/.txt`.
- Conséquence : **`.pdf` et `.docx` sont acceptés partout**, `.docx` et `.pdf` sont les deux seuls formats à produire, et une cible de poids < 300 Ko met toutes les limites hors sujet.

**Confiance moyenne**, source secondaire non méthodologiquement documentée : les taux de réussite d'analyse par mise en page (DOCX en texte simple 96–100 %, PDF standard 90–95 %, DOCX avec tableaux ~69 %, mise en page multi-colonnes 35–50 %, export d'outil de design < 5 %). Les ordres de grandeur concordent avec le consensus du domaine ; les chiffres exacts ne sont pas fiables. **La règle de conception qui en découle est, elle, solide et peu risquée** : une colonne, pas de tableau, pas de zone de texte, pas d'en-tête ni de pied de page, coordonnées dans le corps, intitulés de rubriques standards, police standard.

**Supposé** : qu'aucun des employeurs ciblés n'exige un format exotique. La phase 2 remonte `accepted_formats` et le renderer s'y conforme ; si un format hors `{pdf, docx, txt, rtf}` est exigé → `held`.

### 7.2 Règles de mise en page

Une seule colonne. Aucun tableau, aucune zone de texte, aucune image, aucun logo, aucune icône, aucun glyphe décoratif. Coordonnées en texte dans le corps, en haut. En-tête et pied de page vides. Puces standard. Calibri ou Arial, corps 10,5–11 pt, interligne 1,08, marges 1,5–2 cm. Intitulés de rubriques en clair et conventionnels : `Expérience professionnelle` / `Work experience`, `Projets` / `Projects`, `Formation` / `Education`, `Compétences` / `Skills`, `Langues` / `Languages`. Pas de rubrique « À propos » ni de titre inventif. URL en texte complet, pas en lien masqué.

### 7.3 Chaîne de rendu, une seule source

```
RenderBundle ──→ python-docx ──→ cv.docx ──┬─→ soffice --headless --convert-to pdf ──→ cv.pdf
                                            └─→ docx2txt ──→ cv.parsed.txt
```

Un seul moteur de mise en page. Le PDF est **la conversion du DOCX**, jamais un rendu parallèle par ReportLab ou WeasyPrint : deux moteurs divergent, et la divergence est invisible jusqu'au jour où un recruteur ouvre les deux. Le coût est une dépendance LibreOffice ; c'est acceptable et c'est préférable à deux vérités.

`cv.txt` est en outre émis directement par le builder : c'est le texte canonique que le garde-fou analyse.

### 7.4 Test de round-trip, le contrôle de mise en page rendu mesurable

Après rendu, extraction du texte du `.docx` produit, puis assertions :
- chaque `bullet_id` retenu est présent, dans l'ordre du `SelectionPlan` ;
- les coordonnées apparaissent dans les 400 premiers caractères ;
- les intitulés de rubriques apparaissent dans l'ordre attendu ;
- aucun caractère de remplacement ni mojibake (accents français, trémas néerlandais) ;
- `page_count == 1` ;
- taille < 300 Ko ;
- `cv.parsed.txt` et `cv.txt` concordent à la normalisation des espaces près.

Échec → `RENDER_PARSE_MISMATCH`, bloquant. C'est la seule façon de contrôler le comportement d'un analyseur de CV sans accès à un ATS : vérifier que l'ordre de lecture du fichier correspond à l'ordre voulu. Ne prétend pas simuler un ATS réel ; détecte la classe de défauts qui casse le parsing.

### 7.5 Nommage

```
Ukendi_Dan_CV_<EmployerSlug>.<ext>
Ukendi_Dan_Lettre_<EmployerSlug>.<ext>        # fr
Ukendi_Dan_CoverLetter_<EmployerSlug>.<ext>   # en
```

`EmployerSlug` : nom de l'employeur, translittéré ASCII, non alphanumériques en `_`, 24 caractères maximum. Nom de famille en premier pour le tri côté recruteur. Pas de date, pas de numéro de version, pas d'identifiant interne : un nom de fichier contenant `v3` ou `run_1142` signale une candidature industrialisée. Si la phase 2 remonte une convention imposée, elle gagne.

Les artefacts internes (`cv.txt`, `plan.json`, `guard_report.json`) portent des noms techniques et ne quittent jamais le disque local.

---

## 8. Contrat de données

### 8.1 Tables SQLite

```
profile_versions(
  profile_version TEXT PRIMARY KEY,     -- short hash
  content_hash TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  loaded_at TEXT NOT NULL)

generation_runs(
  run_id TEXT PRIMARY KEY,
  offer_id TEXT NOT NULL REFERENCES offers(offer_id),
  application_plan_id TEXT,             -- phase 2
  profile_version TEXT NOT NULL REFERENCES profile_versions(profile_version),
  model_name TEXT NOT NULL,
  model_digest TEXT,
  prompt_version TEXT NOT NULL,
  doc_language TEXT NOT NULL,
  language_source TEXT NOT NULL,        -- detector | plan | fallback | downgrade
  tier TEXT NOT NULL,                   -- a | b | c
  started_at TEXT, finished_at TEXT,
  status TEXT NOT NULL,                 -- running | clean | held | failed
  hold_reason TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER,
  llm_calls INTEGER, wall_ms INTEGER)

selection_plans(
  plan_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
  plan_json TEXT NOT NULL,
  fact_ids_json TEXT NOT NULL,
  gap_skills_json TEXT NOT NULL,
  prescore_json TEXT NOT NULL,
  llm_reordered INTEGER NOT NULL)       -- 0 when --no-llm-plan

documents(
  document_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
  offer_id TEXT NOT NULL,
  doc_type TEXT NOT NULL,               -- cv | cover_letter
  language TEXT NOT NULL,
  format TEXT NOT NULL,                 -- docx | pdf | txt
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  word_count INTEGER,
  page_count INTEGER,
  is_primary INTEGER NOT NULL)

question_answers(
  answer_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
  question_key TEXT NOT NULL,           -- from phase 2
  question_text TEXT NOT NULL,
  category TEXT NOT NULL,
  answer_text TEXT,
  answer_value TEXT,                    -- for closed fields
  source TEXT NOT NULL,                 -- declaration | computed | claims_bank | llm
  char_limit INTEGER,
  requires_human INTEGER NOT NULL)

guard_findings(
  finding_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
  document_id TEXT,
  answer_id TEXT,
  layer TEXT NOT NULL,                  -- l0..l4
  code TEXT NOT NULL,
  severity TEXT NOT NULL,
  span_start INTEGER, span_end INTEGER,
  excerpt TEXT, detail TEXT)

application_packages(
  package_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES generation_runs(run_id),
  offer_id TEXT NOT NULL,
  status TEXT NOT NULL,                 -- draft | approved | held | superseded
  hold_reason TEXT,
  tier TEXT NOT NULL,
  requires_human_review INTEGER NOT NULL,
  approved_by TEXT, approved_at TEXT,
  manifest_path TEXT NOT NULL,
  created_at TEXT NOT NULL)
```

Index : `generation_runs(offer_id, status)`, `documents(run_id)`, `guard_findings(run_id, severity)`, `application_packages(status, tier)`.

Un seul paquet actif par offre : un nouveau run passe le précédent en `superseded`. Les anciens artefacts sont conservés, c'est la matière de l'évaluation.

### 8.2 Modèles Pydantic

Entrée : `OfferRecord`, `ApplicationPlan`, `RequiredField`, `ExpectedFile`, `CustomQuestion`, `EmployerFact`.
Profil : `Profile`, `Identity`, `LanguageSkill`, `EducationEntry`, `ExperienceEntry`, `ProjectEntry`, `Bullet`, `Metric`, `Skill`, `ClaimVariant`, `CvTitle`, `NeverClaimRule`, `Declarations`.
Travail : `OfferContext`, `DocumentLanguage`, `SelectionPlan`, `LetterBlocks`, `AnswerSet`, `Answer`.
Sortie : `RenderBundle`, `DocumentSpec`, `RenderedDocument`, `Violation`, `GuardReport`, `ApplicationPackage`, `PackageManifest`.

Tous en `model_config = ConfigDict(extra="forbid", frozen=True)`. `extra="forbid"` est essentiel sur les modèles désérialisés depuis une sortie LLM : un champ en trop doit faire échouer la validation, pas passer silencieusement.

### 8.3 Artefacts sur disque

```
artifacts/<offer_id>/<run_id>/
  manifest.json
  plan.json
  cv.txt  cv.docx  cv.pdf
  letter.txt  letter.docx  letter.pdf
  answers.json
  guard_report.json
  llm_calls/<stage>.<n>.json      -- prompt, réponse, modèle, tokens, durée
  outbox/
    Ukendi_Dan_CV_<EmployerSlug>.pdf
    Ukendi_Dan_Lettre_<EmployerSlug>.pdf
```

`outbox/` contient exactement les fichiers à soumettre, avec leur nom final. La phase 4 ne lit que `manifest.json` et `outbox/`. `llm_calls/` conserve chaque appel : indispensable pour constituer les cassettes et pour l'audit a posteriori.

`manifest.json` : `offer_id`, `run_id`, `profile_version`, `model_name`, `model_digest`, `prompt_version`, `doc_language`, `tier`, `status`, `requires_human_review`, `files[]` (role, path, sha256, bytes, format), `answers[]`, `guard_summary`, `created_at`.

---

## 9. Évaluation

Reprise de l'esprit du harnais de la phase 1 : un vrai run exporté en format annotable, puis des chiffres reproductibles.

### 9.1 Jeu de cas

`eval/phase3/cases.jsonl`, 30 à 40 offres issues de vrais runs de phase 1, stratifiées : langue (fr / en / nl), famille de poste (GenAI, data engineering, data analyst, MLOps), type d'employeur (startup, grand groupe, consultance, institution), et richesse du plan de phase 2 (plan riche / plan pauvre / questions sensibles). Un cas = l'offre figée + l'`ApplicationPlan` figé + le `profile_version` de référence. Les offres sont figées sur disque : une offre dépubliée ne doit pas casser l'évaluation.

### 9.2 Métriques automatiques — la décision se prend là

| Métrique | Définition | Seuil |
|---|---|---|
| `fabrication_blocking` | violations bloquantes L0–L3 par document | **0, strictement** |
| `unsupported_keyword_rate` | compétences du document absentes du profil | **0, strictement** |
| `l4_unsupported_rate` | phrases jugées non étayées | à minimiser, indicatif |
| `keyword_coverage` | exigences de l'offre réellement détenues et présentes dans le document | plus haut vaut mieux |
| `banned_phrase_hits` | formules de la liste noire | 0 |
| `letter_cross_offer_overlap` | Jaccard des 4-grammes entre lettres de deux offres différentes du même modèle | **< 0,35** |
| `offer_evidence_grounding` | le bloc `hook` cite-t-il un span réel de l'offre | 100 % |
| `fit_block_omission_rate` | part de lettres où `fit` est omis faute de faits employeur | à surveiller, pas à minimiser |
| `page_count_ok`, `round_trip_ok`, `language_match`, `word_count_in_band`, `format_allowed` | booléens | 100 % |
| `held_rate` | part d'offres bloquées | à surveiller : trop bas = garde-fou mou |
| `wall_ms_p50/p95`, `llm_calls`, `tokens` | coût | budget section 10 |

`letter_cross_offer_overlap` est la métrique la plus informative du lot et celle que personne ne pense à mesurer. Une lettre « adaptée » dont 60 % des 4-grammes se retrouvent dans la lettre envoyée à un autre employeur n'est pas adaptée : c'est un gabarit. Elle mesure exactement ce qui fait qu'un recruteur sent le générique.

### 9.3 Rubrique humaine — inévitable

Les métriques automatiques ne mesurent pas « est-ce que ça sonne écrit par une machine ». Donc : 10 cas, notation en aveugle par Dan, 1 à 5 sur quatre axes — spécificité à l'employeur, crédibilité de la voix, absence de tells IA, « est-ce que j'envoie ça ». Comparaison A/B appariée sur les mêmes offres, étiquettes de modèle masquées, ordre randomisé, résultats stockés dans `eval/phase3/human_ratings.jsonl`. 10 cas × 2 modèles = 20 lectures, environ 45 minutes. C'est le seul investissement humain récurrent du dispositif et il est non substituable.

### 9.4 Protocole de comparaison de modèles

```
jobpipe eval phase3 --cases eval/phase3/cases.jsonl \
  --model qwen2.5:7b --model qwen3:8b \
  --prompt-version v1 --seed 17 --repeats 2 --report out/phase3-eval.md
```

Température 0,2 pour l'étage A (sortie structurée), 0,6 pour l'étage B (prose). `--repeats 2` pour exposer la variance : un écart entre deux modèles inférieur à la variance intra-modèle n'est pas un écart. Mêmes cas, mêmes prompts, même `profile_version` — le rapport refuse de comparer deux runs dont le `prompt_version` ou le `profile_version` diffère. Sortie : table Markdown, une ligne par modèle, et une annexe des régressions par cas.

Le mode `--no-llm-plan` est une troisième colonne obligatoire du tableau : il faut savoir si l'étage A apporte quelque chose.

---

## 10. Budget de calcul

### 10.1 Appels et contexte

| Étage | Appels | Prompt | Complétion |
|---|---|---|---|
| A — `select` | 1 (+1 retry possible) | ~2 200 | ~500 |
| B — `letter` | 1 | ~2 400 | ~450 |
| B — `cv_summary` | 1 | ~1 100 | ~110 |
| B — `answers` (ouvertes) | 0 à 2 | ~1 000 | ~200 |
| L4 — `judge` | 1 (optionnel) | ~2 000 | ~250 |

**3 à 6 appels par offre, médiane 4.** Contexte plafonné à **4 096 tokens**, corps de l'offre tronqué aux champs extraits par la phase 2 plus environ 1 200 tokens de texte brut. Ce plafond n'est pas un détail : sur 8 Go de VRAM, le cache KV est la variable qui décide si le modèle reste entièrement sur le GPU. Passer à 8 192 tokens de contexte force un déchargement partiel sur CPU et divise le débit par trois ou quatre.

### 10.2 Modèle recommandé

**`qwen3:8b` en Q4_K_M**, avec `qwen2.5:7b` conservé comme référence à battre.

Raisonnement : à Q4_K_M, un 8B pèse environ 5,5 Go, ce qui laisse de la marge pour le cache KV à 4 K de contexte sur 8 Go. Les sources consultées désignent Qwen3 8B comme le meilleur choix multilingue de sa classe, « nettement meilleur que la famille Llama à taille égale » (**confiance moyenne** : source secondaire, pas un benchmark multilingue mesuré). La famille Qwen est déjà en place, le passage de qwen2.5 à qwen3 est un `ollama pull`, et le harnais existe pour trancher sur des chiffres — ce qui est exactement la bonne manière de régler la question.

Ce qui ne passe pas, et il faut le dire franchement : **la classe 12B est hors budget**. Mistral Nemo 12B et Gemma 3 12B en Q4_K_M demandent environ 8,0 à 8,1 Go **pour les poids seuls** (**vérifié** sur une table de besoins VRAM, section 13). Sur une RTX 5050 Laptop 8 Go, cela signifie déchargement partiel sur CPU dès le premier token de cache. C'est frustrant, parce que Mistral Nemo est précisément le candidat le plus crédible pour de la prose française de qualité. Deux options honnêtes : accepter un run lent et le mesurer une fois pour savoir ce qu'on perd en restant en 8B ; ou tester Mistral Nemo en Q3_K_M (~6,2 Go), en sachant qu'une quantification agressive dégrade d'abord la fluidité dans les langues non anglaises — exactement ce qu'on cherche à préserver. À mesurer, pas à décider à l'avance.

Pour le néerlandais, la question est tranchée en amont par la section 5 : aucun modèle de cette classe n'écrira un néerlandais professionnel crédible, et c'est pourquoi le pipeline n'en génère pas.

### 10.3 Temps pour un lot de 20 offres

Estimation, pas mesure. Hypothèses : Q4 8B entièrement en VRAM, 25–45 tok/s en génération, 400–900 tok/s en préremplissage.

- Complétion par offre, médiane : ~1 300 tokens → **30 à 55 s**
- Préremplissage par offre : ~7 700 tokens → **9 à 20 s**
- Rendu : 2 documents × (docx ~0,3 s + conversion PDF) → **2 à 8 s** avec une instance `soffice` maintenue chaude, 10 à 20 s sans
- Garde-fou L0–L3 : < 1 s
- **Par offre : 45 à 90 s** sans juge L4, **60 à 115 s** avec

**Lot de 20 offres : 15 à 30 minutes sans L4, 20 à 40 minutes avec.** Prévoir 45 minutes de budget réel. Enchaîné à la phase 1 (10 à 16 min déjà mesurés) et à la phase 2, un cycle complet se situe autour d'une heure — c'est un travail de nuit, pas quelque chose qu'on lance avant de partir.

Marges d'erreur larges et assumées : le débit réel sur ce GPU portable dépend du profil thermique, et ces chiffres sont extrapolés, non mesurés. Le harnais doit journaliser `wall_ms` dès le premier run pour les remplacer par des mesures.

Levier le plus rentable si c'est trop lent : désactiver L4 et réduire `answers` aux questions obligatoires. Le préremplissage est déjà au plafond utile.

---

## 11. Points de franchise

### 11.1 Qualité réellement atteignable sans intervention humaine

- **Le CV adapté : bon, et automatisable entièrement.** Parce que ce n'est pas de la génération. C'est de la sélection dans un corpus que Dan a écrit, rendue dans une mise en page propre. Un CV ainsi produit sera meilleur que la plupart des CV adaptés à la main, parce qu'il sera cohérent, parsable et à jour. Je ne vois pas de raison d'y mettre un humain.
- **La lettre de motivation : 3 sur 5, et ça ne montera pas beaucoup.** Un modèle 8B en local produira du français grammaticalement correct, structuré, et reconnaissablement gabarité. Il ne peut pas être spécifique au-delà de ce que contient le texte de l'offre : il n'a pas utilisé le produit de l'employeur, n'a parlé à personne, n'a pas de raison de vouloir *ce* poste-là. La spécificité est la seule chose qui fait lire une lettre, et c'est exactement ce que l'automatisation ne peut pas fabriquer honnêtement. Le bloc `fit` omis quand les faits manquent est la version intègre de ce constat, pas sa solution.
- **Le risque de tell IA est asymétrique et sous-estimé.** En novembre 2026, un recruteur belge qui lit des candidatures juniors en IA en voit passer un volume important de générées, et apprend vite à les repérer. Le coût d'une lettre ignorée est nul. Le coût d'une lettre *identifiée comme machine* par un CTO de startup de huit personnes est la perte de cet employeur, durablement, y compris pour une candidature ultérieure où Dan aurait été le bon candidat. Bruxelles est un marché petit et les cercles IA/data y sont étroits. L'espérance de gain d'un envoi automatique en masse n'est pas évidemment positive sur les offres qui comptent.

### 11.2 Ce qui réduit le tell, concrètement

Liste noire bloquante, `style/banned_phrases.<lang>.txt`, maintenue par Dan : « dans un monde en constante évolution », « c'est avec un grand intérêt », « je suis convaincu que mon profil », « passionné par », « allier rigueur et créativité », « delve », « leverage », « in today's rapidly evolving », « I am excited to », « testament to ». Plus : interdiction du tiret cadratin, interdiction des questions rhétoriques, interdiction des triades d'adjectifs, plafond d'une subordonnée relative par phrase, et interdiction d'ouvrir par « Je ». Ces règles sont des lints déterministes, donc testables et stables, contrairement à une consigne de prompt que le modèle oublie.

Et le levier le plus efficace, qui n'est pas technique : les lettres les plus crédibles seront celles où Dan a écrit lui-même les phrases de `claims_bank`. La banque de formulations n'est pas qu'un dispositif anti-fabrication, c'est le seul endroit où sa voix entre dans le système.

### 11.3 Volume contre qualité — l'arbitrage honnête

La valeur réelle de jobpipe n'est pas d'écrire des lettres. C'est de supprimer les 15 à 25 minutes de remplissage de formulaire par candidature et de ne jamais laisser passer une offre pertinente. Le CV adapté est un gain net. La lettre est l'endroit où l'automatisation est la plus faible et le risque le plus élevé. Un pipeline qui envoie 20 candidatures médiocres par semaine produit probablement moins d'entretiens que 6 candidatures dont 2 sont vraiment travaillées — et il brûle de surcroît les employeurs les plus intéressants.

**Proposition : trois niveaux, pilotés par le score d'offre de la phase 1.**

| Tier | Part | Comportement |
|---|---|---|
| A — offres qu'il veut vraiment (~15–20 % du haut du score) | revue humaine **obligatoire** avant envoi | CV automatique, lettre générée comme brouillon, Dan édite |
| B — offres pertinentes | envoi automatique si garde-fou propre **et** `letter_cross_offer_overlap < 0,35` | |
| C — reste, candidatures de volume | CV + lettre courte, sobre et délibérément factuelle (~120 mots), ou pas de lettre là où elle est optionnelle | |

Une lettre de 120 mots honnête et plate vaut mieux qu'une lettre de 300 mots qui essaie d'avoir de l'enthousiasme et échoue. C'est contre-intuitif et c'est vrai.

### 11.4 Où placer la validation humaine

Une commande `jobpipe review`, entre phase 3 et phase 4, qui liste les `application_packages` en `status='draft'` avec `requires_human_review=1`, affiche la lettre en texte, le diff par rapport à la lettre précédente du même tier, le rapport du garde-fou et les réponses sensibles, et exige un `approve` explicite écrivant `approved_by='human'` et `approved_at`. La phase 4 refuse tout paquet tier A sans ces champs.

Coût : environ 3 à 5 minutes par offre tier A, soit 10 à 20 minutes par lot hebdomadaire. C'est peu.

**Point de friction à signaler explicitement : cela contredit une intention déjà exprimée.** Il est noté dans ce que je sais du projet (information issue de la mémoire de l'utilisateur, fichier sur le pipeline de candidatures) que Dan veut que le pipeline postule sans son intervention, en envoi autonome, sans validation manuelle. Mon analyse conclut qu'une validation humaine sur le haut du panier est nécessaire — pas pour des raisons de sécurité technique, mais parce que c'est là que l'automatisation détruit le plus de valeur. Ce n'est pas à moi de trancher : le portillon est **configurable** (`review.required_tiers = [a]` par défaut, `[]` pour du tout-automatique), et la décision revient à Dan. Je recommande de mesurer : un mois en tout-automatique, taux de réponse par tier, puis un mois avec revue sur tier A. Le pipeline a déjà la journalisation pour répondre à cette question avec des chiffres.

### 11.5 Autres franchises

- **Le diplôme interrompu doit être arbitré une fois par un humain, pas par offre.** La formulation de `edu.ulb` et de `claim.degree_context` est une décision de positionnement. Le filtre diplôme pèse lourd en Belgique sur les profils juniors ; omettre l'ULB laisse un trou qu'un recruteur relèvera, le revendiquer est une fraude. La seule voie est une formulation exacte, écrite par Dan, figée dans le profil. Aucun LLM ne doit improviser là-dessus.
- **Le garde-fou n'attrape pas tout.** L1 et L2 attrapent les spécificités inventées — entités et chiffres — qui sont la classe dangereuse et vérifiable. Ils n'attrapent pas le cadrage faux mais vague : « j'ai encadré une équipe de développeurs », « j'ai conçu l'architecture », qui ne contient ni entité ni chiffre nouveau. L3 couvre les formes connues et dangereuses ; le reste est un risque résiduel réel, qui ne se réduit que par l'enrichissement continu du corpus adverse. Prétendre à une garantie totale serait faux.
- **Le juge L4 sur 8 Go est faible.** Faux positifs et faux négatifs. Utile comme générateur de cas de test, pas comme autorité.
- **La décision néerlandaise coûte des offres.** C'est le bon arbitrage, mais c'est un coût réel sur un marché bruxellois où beaucoup d'employeurs publient en néerlandais.
- **`profile.yaml` est un point de défaillance unique, humain.** Une date erronée s'y propagera dans toutes les candidatures avec une apparence de rigueur. Une relecture attentive avant la première campagne vaut plus que n'importe quelle couche de garde-fou.

---

## 12. Plan d'implémentation suggéré

1. `profile/profile.yaml` + `declarations.yaml` + modèles Pydantic + chargeur avec hash. Testable immédiatement, aucun LLM.
2. Garde-fou L0–L3 + corpus adverse et corpus propre. Avant toute génération : c'est l'oracle de tout le reste.
3. Renderer `.docx`/`.pdf` + test de round-trip + boucle de longueur. Toujours sans LLM.
4. Pré-score déterministe + `build_cv_doc()`. À ce point, un CV adapté complet existe sans aucun appel LLM. Jalon à valider avec Dan.
5. Routeur de langue + `answer_questions()` déterministe.
6. Étage A, `CassetteClient` d'abord, `OllamaClient` ensuite.
7. Étage B, lettre.
8. Harnais d'évaluation + jeu de cas + comparaison qwen2.5 / qwen3 / `--no-llm-plan`.
9. L4, `jobpipe review`, contrat de sortie vers la phase 4.

L'ordre importe : le garde-fou et le renderer avant les appels LLM. Construire la génération en premier conduit à juger la qualité à l'œil, et c'est précisément ce que Dan veut éviter.

---

## 13. Sources et niveau de confiance

**Vérifié — formats acceptés par les ATS**
- [Greenhouse — Supported file types for resumes and cover letters](https://support.greenhouse.io/hc/en-us/articles/360052218132-Supported-file-types-for-resumes-and-cover-letters) : `.doc`, `.docx`, `.pdf`, `.rtf`, `.txt`, 100 Mo. Source éditeur, confiance haute.
- [Kombo — ATS attachment restrictions](https://docs.kombo.dev/ats/attachment-restrictions) : table par ATS (Workday, Greenhouse, Personio, SmartRecruiters, Softgarden, Recruitee, Dvinci), formats et limites de taille testés. Source d'intégrateur, confiance haute sur les formats, moyenne sur les limites exactes.

**Confiance moyenne — comportement des analyseurs de CV face à la mise en page**
- [Jobmentis — Resume parsing guide 2026](https://www.jobmentis.com/guide/resume-parsing) : taux de réussite par mise en page (DOCX simple 96–100 %, PDF standard 90–95 %, DOCX avec tableaux 69 %, multi-colonnes 35–50 %, export Canva/Figma < 5 %), en-têtes/pieds de page peu fiables, polices standard, < 200 Ko. Méthodologie non documentée, aucun éditeur d'ATS nommé : les chiffres exacts ne sont pas fiables, la direction concorde avec le consensus et les règles de conception qui en découlent sont peu risquées.

**Vérifié — conventions belges de candidature**
- [EURES — Vivre et travailler en Belgique](https://eures.europa.eu/living-and-working/living-and-working-conditions-europe/living-and-working-conditions-belgium_fr) : pas de photo sauf demande explicite ; ne pas mentionner l'âge, la nationalité, la situation familiale ni le sexe ; téléphone avec indicatif international ; titre indiquant le poste visé avec les intitulés belges ; expériences antéchronologiques ; rubrique langues séparée avec niveaux CECR. Source officielle UE, confiance haute.
- [Connexion-Emploi — Le CV en Belgique](https://www.connexion-emploi.com/fr/a/le-cv-en-belgique-nos-conseils-de-redaction-de-candidature) : ne pas mentionner nationalité, adresse postale complète, convictions, état civil ; niveaux CECR plutôt que « lu, parlé, écrit » ; **« en cas de doute, envoyez votre candidature en anglais »**. Confiance moyenne-haute.
- Longueur du CV (une page pour un junior) et formules de politesse belges : **non sourcé de façon satisfaisante**. Aucune des sources consultées ne donne de règle chiffrée. Je m'appuie sur la pratique du marché, confiance moyenne, à confirmer par Dan qui connaît le terrain mieux que ces pages.

**Confiance moyenne à faible — modèles ouverts sous 8 Go de VRAM**
- [Pactentia — Best open source LLM for 8GB VRAM 2026](https://pactentia.com/blog/best-open-source-llm-8gb-vram-2026) (mai 2026) : Qwen3 8B en Q4_K_M ~5,5 Go, 128 K de contexte, « excellent multilingual support — far better than Llama-family models at the same size ». Source secondaire, pas de benchmark multilingue mesuré.
- [llmrun.dev — Gemma 3 12B IT vs Mistral Nemo Instruct 2407](https://llmrun.dev/compare/google-gemma-3-12b-it-vs-mistralai-mistral-nemo-instruct-2407) : 12,2 B tous deux, ~8,0 et ~8,1 Go en Q4_K_M. Besoin VRAM, confiance haute ; silence sur les langues européennes.
- **Non vérifié** : aucune source consultée ne mesure la qualité rédactionnelle en français ou en néerlandais de ces modèles. C'est précisément pourquoi la section 9 impose une comparaison mesurée sur son matériel plutôt qu'un choix sur réputation. Je recommande qwen3:8b comme point de départ, pas comme conclusion.

---

## Hypothèses sur le contrat de la phase 2

- La phase 2 fournit, pour chaque offre, exactement un objet `ApplicationPlan` identifié par `application_plan_id` et lié à `offer_id`.
- `ApplicationPlan.expected_files[]` existe et chaque entrée porte `role` (`cv` | `cover_letter` | `other`), `required` (booléen), `accepted_formats[]` (extensions en minuscules, sans point) et `max_bytes` (entier ou nul).
- Quand `accepted_formats` est absent ou vide, la phase 3 est autorisée à produire `.pdf` par défaut.
- `ApplicationPlan.custom_questions[]` existe et chaque entrée porte `question_key` (stable entre deux analyses de la même offre), `question_text` (texte exact affiché), `field_type` (`text` | `textarea` | `select` | `multiselect` | `boolean` | `number` | `date` | `file`), `required` (booléen), `char_limit` (entier ou nul) et `options[]` (pour les champs fermés).
- `question_key` est stable : réanalyser la même offre deux fois produit les mêmes clés, ce qui permet de mettre en cache les réponses.
- `ApplicationPlan.required_fields[]` liste les champs d'identité et de contact attendus par le formulaire, avec leurs noms et types, pour que la phase 3 sache si une réponse doit être produite ou si la phase 4 la remplit depuis `declarations`.
- `ApplicationPlan.detected_language` est fourni ou nul ; s'il est fourni, il porte un code ISO 639-1 et un score de confiance, et la phase 3 peut le préférer à sa propre détection.
- `ApplicationPlan.language_requirements[]` est fourni ou vide, et chaque entrée porte un code de langue et un niveau d'exigence (`required` | `preferred` | `mentioned`).
- `ApplicationPlan.employer_facts[]` est fourni ou vide, et chaque entrée porte `text` (une phrase factuelle extraite), `source_url` et `captured_at`. La phase 3 ne fait aucun appel réseau pour enrichir cette liste.
- Quand `employer_facts` est vide, c'est une information et non une erreur : le bloc `fit` de la lettre est omis.
- `ApplicationPlan` expose `offer_body_text`, le texte intégral de l'offre, stable, avec des offsets de caractères utilisables pour référencer un `offer_evidence_span`.
- `ApplicationPlan.required_skills[]` est fourni ou vide, chaque entrée portant un libellé mot pour mot tiré de l'offre et, si disponible, l'offset où il apparaît.
- `ApplicationPlan.file_naming_convention` est fourni ou nul ; s'il est fourni, il prime sur la convention de nommage de la section 7.5.
- `ApplicationPlan.submission_channel` indique `form` | `email` | `external` | `unknown`, et la phase 3 produit les mêmes artefacts dans tous les cas.
- La phase 2 ne produit aucun texte destiné au candidat : elle extrait, elle ne rédige pas. Tout texte dans un champ de `ApplicationPlan` est soit un extrait mot pour mot de la page, soit une valeur énumérée.
- La phase 2 peut échouer sur une offre et émettre un `ApplicationPlan` partiel avec `status = partial` ; la phase 3 traite alors l'offre en `held` si un champ obligatoire manque, et normalement sinon.

---

## Contrat de sortie pour la phase 4

La phase 3 livre, par offre traitée, exactement une ligne `application_packages` et un répertoire d'artefacts. La phase 4 ne lit que `manifest.json` et `outbox/`. Elle ne lit jamais `profile.yaml`, ne relance aucun LLM et ne modifie aucun document.

**Localisation** : `artifacts/<offer_id>/<run_id>/manifest.json`, chemin également stocké dans `application_packages.manifest_path`.

**Contenu de `manifest.json`** :

```
package_id            str
run_id                str
offer_id              str
application_plan_id   str | null
profile_version       str
model_name            str
model_digest          str | null
prompt_version        str
doc_language          str            # ISO 639-1
tier                  "a" | "b" | "c"
status                "approved" | "draft" | "held"
hold_reason           str | null
requires_human_review bool
approved_by           str | null
approved_at           str | null     # ISO 8601
created_at            str            # ISO 8601
files[]
  role                "cv" | "cover_letter" | "other"
  path                str            # relative to the manifest directory, inside outbox/
  filename            str            # exact name to submit
  format              "pdf" | "docx" | "txt"
  sha256              str
  bytes               int
  page_count          int | null
answers[]
  question_key        str
  question_text       str
  category            str
  answer_text         str | null
  answer_value        str | null     # for closed fields: the option to select
  char_limit          int | null
  requires_human      bool
guard_summary
  blocking            int            # must be 0 when status == "approved"
  warnings            int
  codes[]             str
gaps[]                str            # offer requirements not supported by the profile
```

**Règles que la phase 4 doit appliquer, sans exception** :

1. Ne soumettre que si `status == "approved"`.
2. Refuser si `guard_summary.blocking > 0`, même si `status == "approved"`.
3. Refuser si `requires_human_review` est vrai et que `approved_by` est nul.
4. Vérifier le `sha256` de chaque fichier avant envoi ; en cas d'écart, refuser et journaliser.
5. Ne pas répondre à un champ obligatoire absent de `answers[]` : refuser et marquer l'offre comme à traiter par un humain.
6. Ne jamais répondre à une entrée dont `requires_human` est vrai et dont `answer_text` et `answer_value` sont tous deux nuls.
7. Soumettre les fichiers sous le `filename` indiqué, sans le renommer.
8. Écrire le résultat de la soumission dans sa propre table, en référençant `package_id`, sans modifier `application_packages` autrement que par un champ de statut d'envoi qui lui appartient.

**Garantie fournie** : tout fichier présent dans `outbox/` d'un paquet `approved` ne contient que des affirmations factuelles traçables à `profile_version`, à `declarations` ou au texte de l'offre, selon la vérification décrite en section 6.4, dans la limite du risque résiduel reconnu en section 11.5.

---

## Risques et questions ouvertes

**Risques**

- Risque résiduel de fabrication par cadrage vague, sans entité ni chiffre nouveau (« j'ai encadré une équipe »), que L1 et L2 ne peuvent pas détecter. Atténuation : enrichissement continu du corpus adverse, pas élimination.
- Un faux négatif du garde-fou envoie un document faux à un employeur réel. Conséquence non réversible, contrairement à tout le reste du pipeline.
- Risque de lettre perçue comme générée par IA, avec un coût durable sur un marché bruxellois étroit. C'est le risque principal de la phase et il n'est pas technique.
- `profile.yaml` est un point de défaillance humain unique : une erreur de date ou de chiffre s'y propage dans toutes les candidatures.
- Dépendance LibreOffice pour la conversion PDF : si `soffice` n'est pas disponible ou change de comportement, le contrôle de pagination tombe. Atténuation : échec explicite, jamais de bascule silencieuse vers un second moteur.
- Le garde-fou peut devenir trop strict et bloquer des documents corrects. Surveillé par `held_rate` et par le corpus `tests/fixtures/clean/`.
- Les estimations de temps de la section 10.3 sont extrapolées, pas mesurées ; un facteur 2 est plausible dans les deux sens.
- Une évolution du contrat de la phase 2 casse silencieusement la phase 3 si les modèles Pydantic ne sont pas en `extra="forbid"`. C'est pour cela qu'ils le sont.
- Le classifieur de questions a une catégorie `unknown` par défaut : si la phase 2 remonte beaucoup de questions atypiques, le taux de blocage peut rendre l'automatisation illusoire sur certains portails.

**Questions ouvertes nécessitant une décision de Dan**

- Validation humaine sur les offres de tier A : mon analyse la recommande, et cela contredit l'intention d'un envoi entièrement autonome. Décision à prendre, ou à mesurer sur deux mois.
- Formulation exacte du cursus ULB interrompu et de `claim.degree_context`. À écrire à la main, une fois.
- Prétentions salariales : fournir une fourchette stockée dans `declarations`, ou bloquer systématiquement les offres qui l'exigent.
- Comportement sur filtre éliminatoire d'années d'expérience : `skip` ou `held`.
- Génération en néerlandais : confirmer l'interdiction par défaut, ou demander une mesure de qualité avant de trancher.
- Nombre de puces de `claims_bank` qu'il est prêt à écrire au départ. En dessous d'environ 60, la sélection manquera de matière et les CV se ressembleront entre offres de familles différentes.
- Format par défaut quand l'offre accepte `.pdf` et `.docx` : `.pdf` garantit la mise en page vue par un humain, `.docx` s'analyse mieux. Ma recommandation est `.pdf` par défaut, `.docx` quand la phase 2 détecte un ATS connu pour parser à l'import — mais cette détection n'existe pas encore dans le contrat de la phase 2.
- Faut-il conserver l'étage A s'il ne bat pas le pré-score déterministe dans l'évaluation. Décision par les chiffres, prévue au protocole.
