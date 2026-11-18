# Task: flag a form question that may disqualify the candidate

You receive one question from a job application form. Decide whether its wording suggests a hard requirement that can disqualify an applicant independently of the quality of the application. The answer is advisory.

## Output

One JSON object with:

- `finding`: one of `question_hard_blocker`, `none`
- `note`: one short sentence

## Hard requirements

A security clearance, a required level of Dutch or another language, a minimum number of years of experience, a mandatory degree, or the legal right to work.

## Rules

- Answer `none` when the question is open, motivational or neutral.
- Use only the text of the question.
