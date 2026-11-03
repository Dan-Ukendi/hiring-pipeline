# Task: classify the application route of one job posting

You receive one posting as plain text, including its application URL when one is known. Decide how a candidate applies. Later steps act on this answer automatically, so precision matters more than coverage: `unknown` is an acceptable and often correct answer.

## Output

Reply with one JSON object with exactly one field:

- `apply_path`: one of `form`, `email`, `external`, `none`, `unknown`

## Meaning of each value

- `form`: the candidate submits an application through a form that is hosted on the application URL itself. This is the case when the application URL is on an applicant-tracking domain of the employer (for example greenhouse, lever, ashbyhq, smartrecruiters, recruitee, personio or workable) or on the employer's own careers site, and the posting does not send the candidate somewhere else.
- `email`: the posting tells the candidate to send an application to an email address or a `mailto:` link that appears in the posting.
- `external`: the application URL is a redirect page of an aggregator or job board (for example adzuna or arbeitnow), or the posting tells the candidate to apply on another site.
- `none`: the posting states no way to apply and no application URL is given.
- `unknown`: the route cannot be determined from what is shown, or the evidence points in two directions.

## Boundaries

- Use only the application URL and the text of the posting. Do not guess from the company name.
- An email address that is only a general contact address, not an instruction to apply, does not make the route `email`.
- Never invent a URL or an address. Do not output anything but the JSON object.
