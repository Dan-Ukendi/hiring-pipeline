# Task: report missing or implausible fields of one job posting

You receive one posting as plain text. Every field has its own line, and a field without a value is shown as `not stated`. Report which of the required fields are missing or implausible.

The required fields are listed below with the label under which each one appears in the posting:

{{required_fields}}

## Output

Reply with one JSON object with exactly one field:

- `missing_fields`: a list of field names taken only from the required fields above. An empty list is the normal answer for a well-formed posting.

## What counts

- Missing: the field's line says `not stated`. A field whose line shows a value is present.
- Implausible: the field shows a value that cannot be right, and you can name the defect: the value contains markup or HTML tags, a description of fewer than ten words, a company name that is a single character or a dummy such as `N/A`, `test` or `company`, a title that is a code or a whole sentence, a date in the future, a location that is not a place.

When in doubt about a field that shows a value, leave it out. Location values such as a list of region names, a postal code with a commune, or `Remote` are plausible. A long description is plausible even when it ends abruptly.

## Boundaries

- Only report. Never suggest, guess or write a value, and never explain.
- Do not judge whether the job is a good fit. Only judge whether the fields are present and plausible.
- A note that the description was truncated is not a defect of the description.
- Do not list a field that is not in the required fields.

## Examples

Posting lines: `Title: Data Engineer`, `Company: not stated`, `Location: Brussels`, `Application URL: https://jobs.example.test/12`, `Posted: 2026-10-20`, a description of several sentences. Answer: `{"missing_fields": ["company_name"]}`

Posting lines: `Title: Data Engineer`, `Company: Acme`, `Location: <b>Brussels</b>`, `Application URL: https://jobs.example.test/12`, `Posted: 2026-10-20`, a description of several sentences. Answer: `{"missing_fields": ["location_raw"]}`

Posting lines: `Title: Machine Learning Engineer`, `Company: Sia`, `Location: Belgium, Brussels Region, Brussels`, `Application URL: https://www.example.test/details/9`, `Posted: 2026-10-26`, a description of several sentences. Answer: `{"missing_fields": []}`
