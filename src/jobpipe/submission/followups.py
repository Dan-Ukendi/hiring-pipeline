from datetime import datetime
from pathlib import Path

from jobpipe.db.observability_store import ObservabilityStore
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.submission.dossier import dossier_dir

DRAFTS = {
    "fr": "Madame, Monsieur,\n\nJe me permets de revenir vers vous au sujet de ma candidature au poste de {title}, envoyée le {date}. "
          "Je reste très intéressé par ce poste et disponible pour un échange.\n\nCordialement,\n{name}\n",
    "en": "Dear Sir or Madam,\n\nI am following up on my application for the {title} position, sent on {date}. "
          "I remain very interested in the role and available for a conversation.\n\nKind regards,\n{name}\n",
}
DRAFT_NAME = "relance-draft.txt"


def prepare_followups(obs: ObservabilityStore, subs: SubmissionStore, now: datetime, after_days: int, root: Path, name: str,
                      language_of: dict[int, str] | None = None) -> list[Path]:
    written: list[Path] = []
    for due in obs.followups_due(now, after_days):
        app = subs.get(due["id"])
        directory = dossier_dir(root, app, due["company_name"], due["title"])
        target = directory / DRAFT_NAME
        if target.exists():
            continue
        directory.mkdir(parents=True, exist_ok=True)
        language = (language_of or {}).get(app.id, "en")
        target.write_text(DRAFTS.get(language, DRAFTS["en"]).format(title=due["title"], date=due["sent_at"][:10], name=name), encoding="utf-8")
        written.append(target)
    return written
