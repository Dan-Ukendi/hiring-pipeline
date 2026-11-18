from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.submission.models import PlannedSubmission

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_planned(planned: list[PlannedSubmission], generated_at: str, titles: dict[str, str]) -> str:
    environment = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape(["html"]),
                              trim_blocks=True, lstrip_blocks=True)
    return environment.get_template("planned.html").render(planned=planned, generated_at=generated_at, titles=titles)
