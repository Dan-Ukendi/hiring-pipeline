# Task: map one application-form label to a known profile key

You receive the label of one field of a job application form. Decide which profile key, if any, the field asks for. The answer is advisory and is reviewed by a person before it is used.

## Output

One JSON object with:

- `suggested_profile_key`: one of the allowed keys below, or null when none fits
- `rationale`: one short sentence

## Allowed keys

{{keys}}

## Rules

- Answer null whenever the label is ambiguous, is a free question to the employer, or asks for something not in the list.
- Use only the label and the field type shown. Never invent a key.
