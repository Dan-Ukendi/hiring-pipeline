# Task: write the variable blocks of a cover letter

You write in the first person singular, in the language named below. Use only the facts provided. Never invent an employer, a number, a school, a certification or a tool. Never say "we". Never excuse the candidate's path.

## Output

One JSON object with:

- `hook`: an object with `text` (35 to 55 words naming the role and one precise element of the offer), `evidence_start` and `evidence_end` (character offsets, in the offer text below, of the exact passage the hook relies on)
- `proof_text`: 90 to 140 words presenting the listed projects with their listed figures
- `proof_project_ids`: the identifiers of the projects used, taken from the list below
- `fit_text`: 50 to 90 words about the employer using only the numbered employer facts, or null when no fact is listed
- `fit_fact_index`: the number of the fact used, or null

## Language

{{language}}

## Offer (title, employer, text)

{{offer}}

## Projects and figures

{{projects}}

## Employer facts

{{facts}}
