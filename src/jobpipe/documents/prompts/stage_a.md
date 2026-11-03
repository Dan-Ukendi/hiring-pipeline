# Task: order the evidence of a CV for one job offer

You receive the pre-scored list of projects, bullets and skills already selected for a candidate, and the requirements of one offer. You may reorder projects and skills, choose one title, and remove bullets that are marked droppable. You can never add anything and never write text.

## Output

One JSON object with:

- `title_id`: one of the title identifiers listed below
- `project_order`: every project identifier listed below, in the order they should appear
- `drop_bullets`: bullet identifiers to remove, only from the droppable list (may be empty)
- `skill_order`: skill identifiers from the list below, best first
- `note`: one short sentence explaining the main choice

## Rules

- Use only identifiers that appear in the input. An unknown identifier invalidates the answer.
- Keep the project list complete: reorder, do not remove projects.
- Prefer evidence that matches the required skills over the preferred ones.

## Input

{{input}}
