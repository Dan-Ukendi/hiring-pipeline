import csv
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.contract.clock import to_brussels
from jobpipe.db.submission_store import ApplicationRow, SubmissionStore

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def slug(value: str | None, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:limit] or "x"


def dossier_dir(root: Path, app: ApplicationRow, company: str | None, title: str | None) -> Path:
    stamp = to_brussels(app.sent_at or app.created_at).strftime("%Y-%m-%d")
    return root / f"{stamp}-{slug(company)}-{slug(title)}-{app.id}"


def write_dossier(subs: SubmissionStore, app: ApplicationRow, root: Path) -> Path:
    snapshot = subs.job_snapshot(app.job_id)
    company = snapshot["company_name"] if snapshot else None
    title = snapshot["title"] if snapshot else None
    directory = dossier_dir(root, app, company, title)
    directory.mkdir(parents=True, exist_ok=True)
    documents = []
    for document in subs.documents(app.id):
        source = subs.artifact_path(document["artifact_sha256"])
        if source is not None and source.is_file():
            shutil.copyfile(source, directory / document["filename_sent"])
        documents.append({"role": document["role"], "filename": document["filename_sent"], "sha256": document["artifact_sha256"]})
    attempts = []
    for attempt in subs.attempts(app.id):
        evidence = []
        for item in subs.evidence(attempt["id"]):
            name = None
            path = subs.artifact_path(item["artifact_sha256"]) if item["artifact_sha256"] else None
            if path is not None and path.is_file():
                extension = ".eml" if item["kind"] in {"sent_message"} else ".json" if item["kind"] == "planned_submission" else ".bin"
                name = f"evidence-{attempt['id']}-{item['kind']}{extension}"
                shutil.copyfile(path, directory / name)
            evidence.append({"kind": item["kind"], "summary": item["summary"], "captured_at": item["captured_at"], "file": name})
        attempts.append({"id": attempt["id"], "route": attempt["route"], "target": attempt["target_url"], "dry_run": bool(attempt["dry_run"]),
                         "started_at": attempt["started_at"], "status": attempt["transport_status"], "evidence": evidence})
    environment = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    html = environment.get_template("dossier.html").render(
        app=app, job=snapshot, contacts=subs.contacts_for(app.job_id), documents=documents, fields=subs.fields(app.id),
        attempts=attempts, events=subs.events(app.id), inbound=subs.inbound(app.id),
    )
    (directory / "dossier.html").write_text(html, encoding="utf-8")
    return directory


def recall(subs: SubmissionStore, query: str, root: Path, limit: int = 10) -> list[tuple[ApplicationRow, Path, str | None, str | None]]:
    results = []
    for app_id in subs.search(query, limit):
        app = subs.get(app_id)
        snapshot = subs.job_snapshot(app.job_id)
        company = snapshot["company_name"] if snapshot else None
        title = snapshot["title"] if snapshot else None
        results.append((app, dossier_dir(root, app, company, title), company, title))
    return results


def export_log(subs: SubmissionStore, target: Path) -> int:
    rows = subs.job_search_log()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "employer", "title", "channel", "state", "outcome", "url"])
        for row in rows:
            sent = datetime.fromisoformat(row["sent_at"])
            writer.writerow([to_brussels(sent).strftime("%Y-%m-%d"), row["company_name"], row["title"], row["route"], row["state"],
                             row["outcome"] or "", row["url"]])
    return len(rows)
