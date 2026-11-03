import re
from importlib import resources

from jobpipe.config import Taxonomy
from jobpipe.models import PostingView

_PARAGRAPHS = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
NOT_STATED = "not stated"


def prompt_variables(taxonomy: Taxonomy) -> dict[str, str]:
    communes = [", ".join([c.name, *c.aliases]) for c in taxonomy.communes]
    return {"brussels_communes": "; ".join(communes)}


def load_prompt(name: str) -> str:
    return resources.files("jobpipe.audit").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


def character_budget(tokens: int, chars_per_token: float) -> int:
    return max(1, int(tokens * chars_per_token))


def truncate_description(text: str | None, budget_chars: int) -> tuple[str | None, bool]:
    if text is None or len(text) <= budget_chars:
        return text, False
    kept: list[str] = []
    used = 0
    for paragraph in _PARAGRAPHS.split(text):
        cost = len(paragraph) + (2 if kept else 0)
        if used + cost > budget_chars:
            break
        kept.append(paragraph)
        used += cost
    if kept:
        return "\n\n".join(kept), True
    sentences = _SENTENCE_END.split(text)
    head: list[str] = []
    used = 0
    for sentence in sentences:
        cost = len(sentence) + (1 if head else 0)
        if used + cost > budget_chars:
            break
        head.append(sentence)
        used += cost
    if head:
        return " ".join(head), True
    cut = text[:budget_chars]
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > 0 else cut), True


def prepare_text(view: PostingView, max_tokens: int, chars_per_token: float) -> tuple[str, bool, int]:
    description, truncated = truncate_description(view.description_text, character_budget(max_tokens, chars_per_token))
    return render_posting(view, description, truncated), truncated, len(description or "")


FIELD_LABELS = {
    "title": "Title",
    "company_name": "Company",
    "location_raw": "Location",
    "remote_policy": "Remote policy",
    "employment_type": "Employment type",
    "language": "Language",
    "posted_at": "Posted",
    "salary_raw": "Salary",
    "apply_url": "Application URL",
    "url": "Posting URL",
    "description_text": "Description",
}


def describe_fields(names: list[str]) -> str:
    return "\n".join(f"- `{name}` (shown as `{FIELD_LABELS.get(name, name)}`)" for name in names)


def render_posting(view: PostingView, description: str | None, truncated: bool) -> str:
    def value(item: object) -> str:
        return NOT_STATED if item in (None, "") else str(item)

    posted = view.posted_at.date().isoformat() if view.posted_at else None
    lines = [
        f"{FIELD_LABELS['title']}: {value(view.title)}",
        f"{FIELD_LABELS['company_name']}: {value(view.company_name)}",
        f"{FIELD_LABELS['location_raw']}: {value(view.location_raw)}",
        f"{FIELD_LABELS['remote_policy']}: {value(view.remote_policy)}",
        f"{FIELD_LABELS['employment_type']}: {value(view.employment_type)}",
        f"{FIELD_LABELS['language']}: {value(view.language)}",
        f"{FIELD_LABELS['posted_at']}: {value(posted)}",
        f"{FIELD_LABELS['salary_raw']}: {value(view.salary_raw)}",
        f"Source: {view.source}",
        f"{FIELD_LABELS['apply_url']}: {value(view.apply_url)}",
        f"{FIELD_LABELS['url']}: {value(view.url)}",
        "Description" + (" (truncated at a paragraph boundary):" if truncated else ":"),
        value(description),
    ]
    return "\n".join(lines)
