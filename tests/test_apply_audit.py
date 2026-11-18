import sqlite3

import pytest

from jobpipe.apply.audit import AdvisoryAuditor
from jobpipe.apply.rules import load_apply_rules
from jobpipe.apply.service import build_plans
from jobpipe.audit.ollama import OllamaUnavailable, StructuredResult
from jobpipe.contract.clock import FixedClock
from jobpipe.db.apply_audit_store import advisory_scope
from tests import apply_pages as pages
from tests.apply_corpus import URL
from tests.test_apply_store_service import NOW, Site, make_builder, make_fetcher, seed


class FakeChat:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, model, system, user, schema, *, job_id=None):
        self.calls.append((model, user, schema))
        outcome = self.reply(schema, user)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def planned_store(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CUSTOM_QUESTIONS_FORM)
    seed(store, [(URL, {})])
    clock = FixedClock(NOW)
    fetcher = make_fetcher(config, tmp_path, site)
    build_plans(store, config, make_builder(config, fetcher), fetcher, clock)
    return clock


def snapshot_tables(store):
    return {
        name: store.connection.execute(f"SELECT * FROM {name} ORDER BY 1, 2, 3").fetchall()
        for name in ("apply_plan", "apply_field", "apply_document_slot", "apply_obstacle")
    }


def valid(schema, user):
    if schema.__name__ == "QuestionFinding":
        return StructuredResult(schema(finding="question_hard_blocker", note="asks about authorization"), "ok", 0, 1)
    return StructuredResult(schema(suggested_profile_key="languages", rationale="mentions spoken languages"), "ok", 0, 1)


def test_valid_advisory_output_is_stored_in_the_advisory_tables_only(config, tmp_path, store):
    clock = planned_store(config, tmp_path, store)
    before = snapshot_tables(store)
    auditor = AdvisoryAuditor(FakeChat(valid), "configured-model", load_apply_rules(config.config_dir), clock)
    with advisory_scope(store.connection) as writer:
        result = auditor.run(writer, 50)
    assert result.suggestions > 0 and result.findings > 0 and result.unusable == 0
    assert snapshot_tables(store) == before
    rows = store.connection.execute("SELECT suggested_profile_key, accepted, model FROM apply_field_suggestion").fetchall()
    assert rows and all(r["accepted"] == 0 and r["model"] == "configured-model" for r in rows)


def test_out_of_enumeration_output_is_unusable_and_changes_no_fact(config, tmp_path, store):
    clock = planned_store(config, tmp_path, store)
    before = snapshot_tables(store)

    def invalid(schema, user):
        return StructuredResult(None, "unknown", 2, 1, "schema-invalid response")

    auditor = AdvisoryAuditor(FakeChat(invalid), "configured-model", load_apply_rules(config.config_dir), clock)
    with advisory_scope(store.connection) as writer:
        result = auditor.run(writer, 50)
    assert result.unusable > 0 and result.suggestions == 0 and result.findings == 0
    assert snapshot_tables(store) == before
    statuses = {r["audit_status"] for r in store.connection.execute("SELECT audit_status FROM apply_audit")}
    assert statuses == {"unusable"}


def test_suggestion_schema_rejects_keys_outside_the_profile(config):
    from jobpipe.apply.audit import suggestion_schema

    schema = suggestion_schema(["email", "phone"])
    assert schema(suggested_profile_key=None, rationale="none fits").suggested_profile_key is None
    with pytest.raises(Exception):
        schema(suggested_profile_key="favourite_colour", rationale="invented")


def test_missing_ollama_does_not_interrupt_the_run(config, tmp_path, store):
    clock = planned_store(config, tmp_path, store)
    auditor = AdvisoryAuditor(FakeChat(lambda s, u: OllamaUnavailable("down")), "m", load_apply_rules(config.config_dir), clock)
    with advisory_scope(store.connection) as writer:
        result = auditor.run(writer, 50)
    assert result.unusable > 0


def test_the_advisory_scope_cannot_write_facts(config, tmp_path, store):
    planned_store(config, tmp_path, store)
    with advisory_scope(store.connection):
        for statement in (
            "INSERT INTO apply_field (job_id, ordinal, field_key, field_class, field_type, is_required, "
            "profile_key_source, needs_human, evidence) VALUES ('x', 0, 'k', 'standard', 'text', 0, 'exact', 0, 'e')",
            "UPDATE apply_plan SET feasibility = 'auto_full'",
            "DELETE FROM apply_obstacle",
            "INSERT INTO job_alias (canonical_key, job_id, source, linked_at) VALUES ('a', 'b', 'c', 'd')",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                store.connection.execute(statement)
    with store.connection:
        store.connection.execute("DELETE FROM apply_obstacle")


def test_prompts_use_the_closed_vocabulary(config):
    rules = load_apply_rules(config.config_dir)
    auditor = AdvisoryAuditor(FakeChat(valid), "m", rules, FixedClock(NOW))
    assert "work_authorization_be" in auditor._suggest_prompt and "{{keys}}" not in auditor._suggest_prompt
