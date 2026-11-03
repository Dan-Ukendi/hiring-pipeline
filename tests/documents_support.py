import json
import shutil
from pathlib import Path

from jobpipe.apply.service import build_plans
from jobpipe.documents.profile import load_profile
from tests.apply_support import NOW, Site, make_builder, make_fetcher, make_job
from jobpipe.contract.clock import FixedClock

ROOT = Path(__file__).resolve().parents[1]
URL = "https://jobs.example-employer.test/careers/42"
OFFER = (
    "Acme Data is hiring a Junior AI Engineer. You will build RAG systems in Python and evaluate LLM answers. "
    "A team of 12 people in Brussels.\n\nYour profile:\n- Python and SQL\n- Kubernetes is a plus\n"
    "Founded in 2012, we have 450 employees in Brussels."
)
EVIDENCE = "build RAG systems in Python"

FORM_WITH_LETTER = """
<html><body><form action="/apply/submit" method="post" enctype="multipart/form-data">
<label for="a">First name</label><input id="a" name="first_name" required>
<label for="b">Email</label><input id="b" type="email" name="email" required>
<label for="c">Resume</label><input id="c" type="file" name="resume" accept=".pdf" required>
<label for="d">Cover letter</label><input id="d" type="file" name="cover_letter" accept=".pdf" required>
<label for="e">Are you legally authorized to work in Belgium?</label>
<select id="e" name="q_auth" required><option value="">Select</option><option>Yes</option><option>No</option></select>
<label for="f">Years of experience with Python (minimum 3 years)</label><input id="f" type="number" name="q_py" required>
<button type="submit">Apply</button></form></body></html>
"""


def example_profile():
    return load_profile(ROOT / "profile" / "profile.example.yaml", ROOT / "profile" / "declarations.example.yaml")


def words(count: int, seed: str) -> str:
    return " ".join([seed] * count)


HOOK = ("I am applying for the Junior AI Engineer role because your team needs people who build RAG systems in Python, "
        "and that is the work I do in my own evaluation project, where I check every answer before anything ships.")
PROOF = ("My Évaluation RAG project is a document question-answering system that I evaluated on 40 hand-verified questions, "
         "and I wrote the evaluation harness before the production code so that every change could be measured. "
         "In a team proof of concept for Client Démo B, I contributed to 120 automated tests with continuous integration, "
         "and for Client Démo A I delivered a text classification pipeline to production across 5 analysis axes. "
         "These projects taught me to connect a customer need to code that can be tested, which is the way I would like to work with your team.")
FIT = ("Your offer says the company was founded in 2012 and employs 450 people in Brussels, which tells me the team has grown "
       "steadily while keeping its engineering work close to the product, and that is the kind of environment where I want to learn.")


def letter_cassette(hook=HOOK, proof=PROOF, fit=FIT, fit_index=0, project_ids=("proj.rag", "proj.poc", "proj.radar")):
    start = OFFER.index(EVIDENCE)
    return {
        "hook": {"text": hook, "evidence_start": start, "evidence_end": start + len(EVIDENCE)},
        "proof_text": proof, "proof_project_ids": list(project_ids), "fit_text": fit, "fit_fact_index": fit_index,
    }


def write_cassettes(directory: Path, *, stage_a=None, letter=None, judge=None, answer=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in (("stage_a", stage_a), ("stage_b", letter), ("judge", judge), ("answer", answer)):
        if payload is not None:
            (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def planned(config, tmp_path, store, form=FORM_WITH_LETTER, description=OFFER, **job_fields):
    site = Site()
    site.add(URL, form)
    run = store.start_run("m", "cfg")
    job = make_job(URL, native_id="1", description=description, title="Junior AI Engineer", company_name="Acme Data",
                   company_slug=None, **job_fields)
    store.upsert_job(job, run.id)
    store.set_job_status(job.job_id, "survivor")
    clock = FixedClock(NOW)
    fetcher = make_fetcher(config, tmp_path / "fetch", site)
    build_plans(store, config, make_builder(config, fetcher), fetcher, clock)
    return job, clock


def isolated_documents_root(tmp_path: Path) -> Path:
    root = tmp_path / "documents"
    if root.exists():
        shutil.rmtree(root)
    return root
