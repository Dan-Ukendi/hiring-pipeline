import json

import pytest

from jobpipe.contract.enums import ApprovedBy, HoldReason, PackageStatus
from jobpipe.contract.gate import documents_ready
from jobpipe.contract.models import PackageManifest, compute_bundle_sha256
from jobpipe.db.documents_store import ApprovalError, DocumentsStore
from jobpipe.documents.llm import CassetteClient, CassetteMissing
from jobpipe.documents.render import soffice_available
from jobpipe.documents.service import generate_documents
from tests.documents_support import (
    OFFER,
    example_profile,
    letter_cassette,
    planned,
    write_cassettes,
)

pytestmark = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")

STAGE_A = {"title_id": "title.ai", "project_order": ["proj.rag", "proj.radar", "proj.poc"], "drop_bullets": [],
           "skill_order": ["skill.python", "skill.rag"], "note": "RAG and Python first"}
ANSWER = {"text": "I would like to join your team"}


def run(config, tmp_path, store, clock, cassettes, **kwargs):
    client = CassetteClient(cassettes) if cassettes is not None else None
    return generate_documents(store, config, example_profile(), clock, client, "configured-model",
                              judge=client if cassettes and (cassettes / "judge.json").exists() else None,
                              output_root=tmp_path / "documents", **kwargs)


def test_clean_generation_is_ready_by_policy_with_a_verifiable_manifest(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    summary = run(config, tmp_path, store, clock, cassettes)
    assert (summary.generated, summary.failed) == (1, 0), summary.errors
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    assert row.status is PackageStatus.READY and row.approved_by is ApprovedBy.POLICY, summary.holds
    manifest = PackageManifest.model_validate_json(open(row.manifest_path, encoding="utf-8").read())
    assert manifest.bundle_sha256 == compute_bundle_sha256(manifest.files)
    assert all(f.path.startswith("/") and f.mime_type == "application/pdf" for f in manifest.files)
    assert {f.role.value for f in manifest.files} == {"resume", "cover_letter"}
    assert documents_ready(manifest, 2)
    assert manifest.files[0].path.endswith("Exemple_Alex_CV_Acme_Data.pdf")
    assert f"/{job.job_id}/{summary.run_id}/outbox/" in manifest.files[0].path
    answers = {a.field_key: (a.value, a.source.value) for a in manifest.answers}
    assert answers["q_auth"] == ("Yes", "declaration")
    assert answers["q_py"] == (0, "computed")


def test_review_is_required_on_tier_a_and_a_human_approval_is_the_only_way_to_ready(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    config.settings.documents.watchlist.append("acme")
    store.connection.execute("UPDATE job SET company_slug = NULL")
    config.settings.review.required_tiers[:] = ["a", "b", "c"]
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    run(config, tmp_path, store, clock, cassettes)
    documents = DocumentsStore(store.connection)
    row = documents.active_for_job(job.job_id)
    assert row.status is PackageStatus.AWAITING_REVIEW and row.approved_by is None
    approved = documents.approve(row.package_id, "owner", clock.now())
    assert approved.status is PackageStatus.READY and approved.approved_by is ApprovedBy.HUMAN
    assert documents.approval_events(row.package_id) == [("approved", "owner")]
    with pytest.raises(ApprovalError):
        documents.approve(row.package_id, "owner", clock.now())


def test_a_held_package_cannot_be_approved(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    bad = letter_cassette(proof="I worked at Accenture for four years on production systems for retail customers.")
    run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=bad))
    documents = DocumentsStore(store.connection)
    row = documents.active_for_job(job.job_id)
    assert row.status is PackageStatus.HELD and row.guard_blocking > 0
    with pytest.raises(ApprovalError, match="held"):
        documents.approve(row.package_id, "owner", clock.now())
    codes = {f["code"] for f in documents.findings(row.package_id)}
    assert "UNSOURCED_ENTITY" in codes


def test_unsupported_sentences_beyond_the_limit_hold_the_package(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette(),
                                judge=[{"verdict": "unsupported"}] * 20)
    run(config, tmp_path, store, clock, cassettes)
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    assert row.status is PackageStatus.HELD and row.guard_blocking == 0 and row.guard_l4_unsupported > 2
    assert row.hold_reason == HoldReason.UNSUPPORTED_CLAIMS.value


def test_unsupported_sentences_within_the_limit_do_not_hold(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    config.settings.documents.l4_unsupported_limit = 20
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette(),
                                judge=[{"verdict": "unsupported"}] * 20)
    run(config, tmp_path, store, clock, cassettes)
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    assert row.status is PackageStatus.READY and row.guard_l4_unsupported > 0


def test_fit_block_is_omitted_when_the_offer_has_no_employer_fact(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store, description="Acme Data is hiring a Junior AI Engineer. You will build RAG systems in Python and evaluate LLM answers.")
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    summary = run(config, tmp_path, store, clock, cassettes)
    assert summary.fit_omitted == 1
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    letter = (tmp_path / "documents" / job.job_id / str(summary.run_id) / "letter.txt").read_text(encoding="utf-8")
    assert "founded in 2012" not in letter and row.status in {PackageStatus.READY, PackageStatus.HELD}


def test_dutch_hard_requirement_holds_with_nl_required_and_documents_stay_english(config, tmp_path, store):
    description = OFFER + "\nDutch is required, fluent Dutch is mandatory."
    job, clock = planned(config, tmp_path, store, description=description, language="nl")
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    summary = run(config, tmp_path, store, clock, cassettes)
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    assert row.status is PackageStatus.HELD and row.hold_reason == "nl_required" and summary.holds["nl_required"] == 1
    manifest = PackageManifest.model_validate_json(open(row.manifest_path, encoding="utf-8").read())
    assert manifest.language == "en" and manifest.language_source.value == "downgrade"


def test_degree_question_always_holds_even_on_tier_c(config, tmp_path, store):
    form = ("<html><body><form action='/go' method='post'><label for='a'>First name</label><input id='a' name='first_name' required>"
            "<label for='b'>Email</label><input id='b' type='email' name='email' required>"
            "<label for='c'>Resume</label><input id='c' type='file' name='resume' accept='.pdf' required>"
            "<label for='d'>Do you have a master's degree?</label><select id='d' name='q_degree' required>"
            "<option value=''>Select</option><option>Yes</option><option>No</option></select>"
            "<button type='submit'>Apply</button></form></body></html>")
    job, clock = planned(config, tmp_path, store, form=form)
    summary = run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette()))
    assert summary.holds["degree_question"] == 1
    assert DocumentsStore(store.connection).active_for_job(job.job_id).status is PackageStatus.HELD


def test_experience_threshold_holds_on_tier_a_and_warns_on_tier_b(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    summary = run(config, tmp_path, store, clock, cassettes)
    documents = DocumentsStore(store.connection)
    row = documents.active_for_job(job.job_id)
    assert row.tier.value in {"b", "c"} and summary.holds["experience_threshold"] == 0
    assert "EXPERIENCE_THRESHOLD_NOT_MET" in {f["code"] for f in documents.findings(row.package_id)}


def test_a_second_generation_supersedes_the_first(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    cassettes = write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette())
    first = run(config, tmp_path, store, clock, cassettes)
    assert run(config, tmp_path, store, clock, cassettes).selected == 0
    store.connection.execute("UPDATE apply_plan SET plan_revision = 2")
    second = run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c2", stage_a=STAGE_A, letter=letter_cassette()))
    assert second.generated == 1
    statuses = [r["status"] for r in store.connection.execute("SELECT status FROM application_packages ORDER BY created_at, package_id")]
    assert sorted(statuses) == ["ready", "superseded"] and first.run_id != second.run_id


def test_no_llm_mode_runs_without_a_model_and_holds_when_a_letter_is_required(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    summary = run(config, tmp_path, store, clock, None)
    assert summary.holds["letter_unavailable"] == 1
    row = DocumentsStore(store.connection).active_for_job(job.job_id)
    assert row.status is PackageStatus.HELD
    assert (tmp_path / "documents" / job.job_id / str(summary.run_id) / "cv.txt").is_file()


def test_the_cv_is_built_from_profile_text_only(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    summary = run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=letter_cassette()))
    cv = (tmp_path / "documents" / job.job_id / str(summary.run_id) / "cv.txt").read_text(encoding="utf-8")
    profile = example_profile().profile
    texts = {b.en for o in (*profile.experience, *profile.projects) for b in o.bullets}
    bullet_lines = [line.removeprefix("• ") for line in cv.splitlines()]
    assert any(line in texts for line in bullet_lines)
    assert "Kubernetes" not in cv


def test_stage_a_output_with_an_unknown_identifier_is_rejected_and_the_pre_score_kept(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    bad = dict(STAGE_A, project_order=["proj.rag", "proj.radar", "proj.invented"])
    run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=[bad, bad], letter=letter_cassette()))
    plan = json.loads(DocumentsStore(store.connection)._conn.execute("SELECT plan_json FROM selection_plans").fetchone()[0])
    assert plan["llm_reordered"] is False and "proj.invented" not in plan["project_ids"]


def test_a_missing_cassette_fails_loudly(tmp_path):
    from jobpipe.documents.stages import LetterOutput

    with pytest.raises(CassetteMissing):
        CassetteClient(tmp_path).chat("m", "s", "u", LetterOutput)


def test_letter_hook_without_evidence_is_blocking(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    broken = letter_cassette()
    broken["hook"]["evidence_end"] = len(OFFER) + 500
    run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=STAGE_A, letter=broken))
    documents = DocumentsStore(store.connection)
    row = documents.active_for_job(job.job_id)
    assert row.status is PackageStatus.HELD
    assert "HOOK_WITHOUT_EVIDENCE" in {f["code"] for f in documents.findings(row.package_id)}


def test_cli_generate_and_review_round_trip(tmp_path, monkeypatch):
    import shutil

    from typer.testing import CliRunner

    from jobpipe.cli import app
    from jobpipe.db.store import Store
    from tests.conftest import PROJECT_ROOT

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    shutil.copytree(PROJECT_ROOT / "profile", tmp_path / "profile")
    (tmp_path / "profile" / "profile.yaml").write_text((tmp_path / "profile" / "profile.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "profile" / "declarations.yaml").write_text((tmp_path / "profile" / "declarations.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8") + "\nreview:\n  required_tiers: [a, b, c]\n", encoding="utf-8")
    database = tmp_path / "data" / "jobpipe.db"
    database.parent.mkdir()
    from tests.apply_support import Site, make_builder, make_fetcher, make_job
    from tests.documents_support import FORM_WITH_LETTER, OFFER, URL
    from jobpipe.apply.service import build_plans
    from jobpipe.config import load_config
    from jobpipe.contract.clock import FixedClock
    from tests.apply_support import NOW

    app_config = load_config(tmp_path / "config")
    app_config.settings.http.default_rate_per_second = 10000.0
    site = Site()
    site.add(URL, FORM_WITH_LETTER)
    with Store.open(database) as store:
        store.init_schema()
        store.migrate()
        run_row = store.start_run("m", "cfg")
        job = make_job(URL, native_id="1", description=OFFER, title="Junior AI Engineer", company_name="Acme Data")
        store.upsert_job(job, run_row.id)
        store.set_job_status(job.job_id, "survivor")
        fetcher = make_fetcher(app_config, tmp_path / "f", site)
        build_plans(store, app_config, make_builder(app_config, fetcher), fetcher, FixedClock(NOW))
    cassettes = write_cassettes(tmp_path / "cassettes", stage_a=STAGE_A, letter=letter_cassette(), judge=[{"verdict": "supported"}] * 30)
    runner = CliRunner()
    base = ["--config", str(tmp_path / "config")]
    result = runner.invoke(app, ["generate", "--cassettes", str(cassettes), "--llm-plan", *base])
    assert result.exit_code == 0, result.output
    assert "generated=1" in result.output and "awaiting_review: 1" in result.output
    listing = runner.invoke(app, ["review", "list", *base])
    package_id = listing.output.split()[0]
    assert package_id.startswith("pkg-")
    approved = runner.invoke(app, ["review", "approve", package_id, *base])
    assert "ready (approved by human)" in approved.output
    again = runner.invoke(app, ["review", "approve", package_id, *base])
    assert again.exit_code == 1
    assert "nothing waiting" in runner.invoke(app, ["review", "list", *base]).output


def test_model_output_reaches_storage_only_through_validated_models(tmp_path):
    import inspect
    import re

    from pydantic import BaseModel

    from jobpipe.documents import stages
    from jobpipe.documents.llm import STAGE_BY_SCHEMA

    schemas = [getattr(stages, name) for name in STAGE_BY_SCHEMA]
    assert all(issubclass(s, BaseModel) and s.model_config.get("extra") == "forbid" for s in schemas)
    directory = tmp_path / "c"
    write_cassettes(directory, letter=dict(letter_cassette(), injected="DROP TABLE job"))
    outcome = CassetteClient(directory).chat("m", "s", "u", stages.LetterOutput)
    assert outcome.value is None and outcome.status == "unknown"
    sources = "".join(inspect.getsource(m) for m in (stages,))
    assert not re.search(r"model_construct|json\.loads", sources)
    from pathlib import Path

    package = Path(stages.__file__).parent
    assert not [p.name for p in package.glob("*.py") if "model_construct" in p.read_text(encoding="utf-8")]


def test_unknown_stage_a_identifiers_never_reach_the_database(config, tmp_path, store):
    job, clock = planned(config, tmp_path, store)
    bad = dict(STAGE_A, skill_order=["skill.kubernetes"])
    run(config, tmp_path, store, clock, write_cassettes(tmp_path / "c", stage_a=[bad, bad], letter=letter_cassette()))
    stored = store.connection.execute("SELECT plan_json FROM selection_plans").fetchone()[0]
    assert "skill.kubernetes" not in stored
