import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jobpipe.contract.clock import FixedClock, in_send_window, parse_iso, to_brussels, to_iso
from jobpipe.contract.enums import (
    ApplyRoute,
    ApprovedBy,
    Feasibility,
    FieldClass,
    FieldType,
    LanguageSource,
    PackageStatus,
    ReasonCode,
    Tier,
)
from jobpipe.contract.gate import documents_ready, language_mismatch, needs_manual
from jobpipe.contract.identity import ats_employer_key, degraded_employer_key, idempotency_key
from jobpipe.contract.models import ApplyField, GuardSummary, PackageFile, compute_bundle_sha256
from jobpipe.contract.tiers import TierInputs, derive_tier
from jobpipe.db.readonly import open_readonly, readonly_scope
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.store import Store
from jobpipe.contract.enums import RunPhase, RunStatus
from tests.contract_factories import NOW, make_bundle, make_field, make_manifest, make_plan, make_side_tables, sha


def test_every_interface_type_compiles_together() -> None:
    bundle = make_bundle()
    manifest = make_manifest()
    side = make_side_tables()
    assert bundle.plan_key == ("greenhouse:adyen:8049101", 1)
    assert manifest.job_id == bundle.plan.job_id
    assert len(side) == 4
    round_trip = type(manifest).model_validate_json(manifest.model_dump_json())
    assert round_trip == manifest
    assert type(bundle).model_validate_json(bundle.model_dump_json()) == bundle


def test_route_enumeration_has_the_eight_values_of_phase_two() -> None:
    assert [r.value for r in ApplyRoute] == [
        "ats_api_submit", "ats_hosted_form", "employer_site_form", "email_apply",
        "account_portal", "aggregator_redirect", "closed", "unknown",
    ]


def test_field_vocabulary_is_unified() -> None:
    assert len(FieldType) == 12
    assert [c.value for c in FieldClass] == ["standard", "custom_question", "compliance", "consent", "document"]
    names = set(ApplyField.model_fields)
    assert {"field_key", "label", "max_length", "is_required", "accept_types", "is_hidden"} <= names
    assert not {"question_key", "question_text", "char_limit", "required", "accepted_formats", "role"} & names


def test_custom_questions_are_a_filter_on_field_class() -> None:
    bundle = make_bundle()
    assert [f.field_key for f in bundle.custom_questions()] == ["why_us", "salary"]
    assert all(f.field_class is FieldClass.CUSTOM_QUESTION for f in bundle.custom_questions())


def test_models_reject_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError):
        make_field(unexpected=True)
    field = make_field()
    with pytest.raises(ValidationError):
        field.label = "changed"


def test_accept_types_are_normalised_extensions() -> None:
    with pytest.raises(ValidationError):
        make_field(accept_types=(".pdf",))
    with pytest.raises(ValidationError):
        make_field(accept_types=("PDF",))
    assert make_field(accept_types=None).accept_types is None


def test_auto_full_is_only_reachable_through_email() -> None:
    with pytest.raises(ValidationError):
        make_plan(feasibility=Feasibility.AUTO_FULL)
    plan = make_plan(
        route=ApplyRoute.EMAIL_APPLY, feasibility=Feasibility.AUTO_FULL, email_recipient="jobs@example.org",
        feasibility_reason_code=ReasonCode.OK,
    )
    assert plan.feasibility is Feasibility.AUTO_FULL
    with pytest.raises(ValidationError):
        make_plan(route=ApplyRoute.EMAIL_APPLY, feasibility=Feasibility.AUTO_FULL)


def test_bundle_hash_covers_the_sorted_file_hashes() -> None:
    manifest = make_manifest()
    assert manifest.bundle_sha256 == compute_bundle_sha256(tuple(reversed(manifest.files)))
    with pytest.raises(ValidationError):
        make_manifest(bundle_sha256=sha("other"))


def test_package_paths_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        PackageFile(role="resume", path="cv.pdf", sha256=sha("x"), mime_type="application/pdf", bytes=1)


def test_status_and_approver_are_coherent() -> None:
    with pytest.raises(ValidationError):
        make_manifest(approved_by=None)
    with pytest.raises(ValidationError):
        make_manifest(status=PackageStatus.HELD)
    held = make_manifest(status=PackageStatus.HELD, approved_by=None)
    assert needs_manual(held)
    awaiting = make_manifest(status=PackageStatus.AWAITING_REVIEW, approved_by=None, tier=Tier.A)
    assert not documents_ready(awaiting, 0)
    human = make_manifest(tier=Tier.A, approved_by=ApprovedBy.HUMAN)
    assert documents_ready(human, 0)


def test_documents_ready_checks_status_blocking_and_unsupported_claims() -> None:
    assert documents_ready(make_manifest(), 0)
    assert not documents_ready(make_manifest(status=PackageStatus.HELD, approved_by=None), 0)
    blocking = GuardSummary(blocking=1, degrading=0, informational=0, l4_unsupported=0)
    assert not documents_ready(make_manifest(guard_summary=blocking), 0)
    unsupported = GuardSummary(blocking=0, degrading=0, informational=0, l4_unsupported=3)
    assert not documents_ready(make_manifest(guard_summary=unsupported), 2)
    assert documents_ready(make_manifest(guard_summary=unsupported), 3)


def test_language_mismatch_treats_the_english_downgrade_as_nominal() -> None:
    assert not language_mismatch("nl", "en", LanguageSource.DOWNGRADE)
    assert not language_mismatch("fr", "fr", LanguageSource.DETECTED)
    assert not language_mismatch(None, "en", LanguageSource.DETECTED)
    assert language_mismatch("nl", "fr", LanguageSource.DOWNGRADE)
    assert language_mismatch("nl", "en", LanguageSource.DETECTED)


def test_idempotency_key_depends_on_every_component() -> None:
    base = idempotency_key("ck", ApplyRoute.EMAIL_APPLY, "jobs@example.org", sha("b"))
    assert base == idempotency_key("ck", ApplyRoute.EMAIL_APPLY, "jobs@example.org", sha("b"))
    assert base != idempotency_key("ck2", ApplyRoute.EMAIL_APPLY, "jobs@example.org", sha("b"))
    assert base != idempotency_key("ck", ApplyRoute.ATS_HOSTED_FORM, "jobs@example.org", sha("b"))
    assert base != idempotency_key("ck", ApplyRoute.EMAIL_APPLY, "other@example.org", sha("b"))
    assert base != idempotency_key("ck", ApplyRoute.EMAIL_APPLY, "jobs@example.org", sha("c"))
    with pytest.raises(ValueError):
        idempotency_key("", ApplyRoute.EMAIL_APPLY, "x", sha("b"))


def test_employer_key_degrades_then_promotes() -> None:
    assert degraded_employer_key("https://careers.example.co.uk/jobs/1", "Example") == "domain:example.co.uk"
    assert degraded_employer_key("https://jobs.acme.com/x", None) == "domain:acme.com"
    assert degraded_employer_key(None, "Acme Data SA") == "name:acme-data"
    assert ats_employer_key("Greenhouse", "Adyen") == "ats:greenhouse:adyen"
    with pytest.raises(ValueError):
        degraded_employer_key(None, None)


@pytest.mark.parametrize(
    ("inputs", "tier"),
    [
        (TierInputs(True, False, "keep", True, ApplyRoute.ATS_HOSTED_FORM), Tier.A),
        (TierInputs(True, False, "keep", False, ApplyRoute.ATS_HOSTED_FORM), Tier.B),
        (TierInputs(True, True, "keep", True, ApplyRoute.ATS_HOSTED_FORM), Tier.C),
        (TierInputs(True, False, "borderline", False, ApplyRoute.EMAIL_APPLY), Tier.C),
        (TierInputs(True, False, None, False, ApplyRoute.UNKNOWN), Tier.C),
        (TierInputs(False, False, None, True, ApplyRoute.EMAIL_APPLY), None),
        (TierInputs(True, False, "keep", True, ApplyRoute.CLOSED), None),
    ],
)
def test_tier_rule_is_deterministic(inputs: TierInputs, tier: Tier | None) -> None:
    assert derive_tier(inputs) is tier


def test_clock_is_injectable_and_converts_to_brussels() -> None:
    clock = FixedClock(datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc))
    assert to_brussels(clock.now()).hour == 7
    clock.advance(hours=2)
    assert to_iso(clock.now()) == "2026-11-03T08:00:00+00:00"
    assert parse_iso(to_iso(clock.now())) == clock.now()
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 3))
    with pytest.raises(ValueError):
        to_iso(datetime(2026, 11, 3))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 3, 6, 59, tzinfo=timezone.utc), False),
        (datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 3, 17, 59, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc), False),
        (datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc), False),
        (datetime(2026, 7, 6, 6, 0, tzinfo=timezone.utc), True),
    ],
)
def test_send_window_uses_brussels_time(moment: datetime, expected: bool) -> None:
    assert in_send_window(moment) is expected


def test_read_only_connection_refuses_writes(tmp_path) -> None:
    path = tmp_path / "ro.db"
    with Store.open(path) as store:
        store.init_schema()
        store.migrate()
    connection = open_readonly(path)
    assert connection.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute("INSERT INTO company (slug, name, created_at, updated_at) VALUES ('a', 'a', 'x', 'x')")
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute("UPDATE run SET status = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute("DROP TABLE job")
    connection.close()


def test_read_only_scope_restores_writes(tmp_path) -> None:
    with Store.open(tmp_path / "scope.db") as store:
        store.init_schema()
        store.migrate()
        conn = store.connection
        with readonly_scope(conn) as guarded:
            with pytest.raises(sqlite3.DatabaseError):
                guarded.execute("DELETE FROM run")
        with conn:
            conn.execute("DELETE FROM run")


def test_open_readonly_requires_an_existing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        open_readonly(tmp_path / "missing.db")


def test_shared_run_ledger_tracks_phase_parent_and_resume(tmp_path) -> None:
    clock = FixedClock(NOW)
    with Store.open(tmp_path / "runs.db") as store:
        store.init_schema()
        store.migrate()
        ledger = RunLedger(store.connection, clock)
        chain = ledger.start(RunPhase.CHAIN, "cfg")
        child = ledger.start(RunPhase.APPLY_PLAN, "cfg", parent_run_id=chain.id)
        ledger.checkpoint(child.id, "job-42")
        assert ledger.interrupted(RunPhase.APPLY_PLAN).last_job_id == "job-42"
        clock.advance(minutes=3)
        ledger.finish(child.id, RunStatus.OK)
        assert ledger.interrupted(RunPhase.APPLY_PLAN) is None
        assert [r.id for r in ledger.children(chain.id)] == [child.id]
        assert ledger.get(child.id).finished_at - ledger.get(child.id).started_at == timedelta(minutes=3)
        day = ledger.on_day(NOW.replace(hour=0), NOW.replace(hour=0) + timedelta(days=1))
        assert [r.id for r in day] == [chain.id, child.id]
        with pytest.raises(KeyError):
            ledger.get(999)
