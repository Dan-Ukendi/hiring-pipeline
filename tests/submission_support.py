import random
from pathlib import Path

from jobpipe.apply.service import build_plans
from jobpipe.contract.clock import FixedClock
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.documents.llm import CassetteClient
from jobpipe.documents.service import generate_documents
from jobpipe.submission.orchestrator import SubmissionOrchestrator, SubmissionSettings
from tests.apply_support import NOW, Site, make_builder, make_fetcher, make_job
from tests.documents_support import OFFER, example_profile, letter_cassette, write_cassettes

STAGE_A = {"title_id": "title.ai", "project_order": ["proj.rag", "proj.radar", "proj.poc"], "drop_bullets": [],
           "skill_order": ["skill.python"], "note": "ok"}


def email_job(store, run_id, native_id="1", recipient="jobs@smallco.be", company="Acme Data", description=OFFER,
              title="Junior AI Engineer", **fields):
    job = make_job(f"https://x.test/{native_id}", apply_url=f"mailto:{recipient}?subject=Junior%20AI%20Engineer", native_id=native_id,
                   description=description, title=title, company_name=company, **fields)
    store.upsert_job(job, run_id)
    store.set_job_status(job.job_id, "survivor")
    return job


def prepared(config, tmp_path, store, jobs=1, companies=None, review_tiers=None, clock=None, cassette_name="c"):
    clock = clock or FixedClock(NOW)
    run = store.start_run("m", "cfg")
    created = [email_job(store, run.id, str(i + 1), recipient=f"jobs{i + 1}@smallco.be",
                         company=(companies[i] if companies else f"Company {i + 1}"),
                         title="Junior AI Engineer" if i == 0 else f"Junior AI Engineer Team {i}") for i in range(jobs)]
    site = Site()
    fetcher = make_fetcher(config, tmp_path / "fetch", site)
    build_plans(store, config, make_builder(config, fetcher), fetcher, clock)
    if review_tiers is not None:
        config.settings.review.required_tiers[:] = review_tiers
    cassettes = write_cassettes(tmp_path / cassette_name, stage_a=STAGE_A, letter=[letter_cassette()] * jobs, judge=[{"verdict": "supported"}] * 40 * jobs)
    client = CassetteClient(cassettes)
    generate_documents(store, config, example_profile(), clock, client, "model", judge=client, output_root=tmp_path / "documents")
    return created, clock


def make_orchestrator(store, tmp_path, clock, settings=None, sender="me@example.org", sleep=None):
    subs = SubmissionStore(store.connection, tmp_path / "artifacts")
    settings = settings or SubmissionSettings(state_dir=tmp_path / "state")
    (tmp_path / "state").mkdir(exist_ok=True)
    return SubmissionOrchestrator(subs, ApplyStore(store.connection), DocumentsStore(store.connection), clock, settings,
                                  "Alex Exemple", sender, random.Random(1), sleep or (lambda _s: None)), subs


def language_of(store):
    apply_store = ApplyStore(store.connection)

    def lookup(job_id):
        row = apply_store.offer_language(job_id)
        return row.language if row else None

    return lookup


def ids(jobs):
    return [j.job_id for j in jobs]


def state_dir(tmp_path: Path) -> Path:
    path = tmp_path / "state"
    path.mkdir(exist_ok=True)
    return path
