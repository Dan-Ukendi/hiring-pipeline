import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jobpipe.contract.clock import parse_iso, to_iso
from jobpipe.contract.enums import (
    ApplyRoute,
    Confidence,
    CredentialKind,
    DocumentSlotKind,
    Feasibility,
    FieldClass,
    FieldType,
    ObstacleKind,
    ProfileKeySource,
    ReasonCode,
    SemanticTag,
    Severity,
    SubmissionPolicy,
    UploadMechanism,
)
from jobpipe.contract.models import (
    ApplyField,
    ApplyPlan,
    ApplyPlanBundle,
    DocumentSlot,
    EmployerContact,
    EmployerFact,
    Obstacle,
    OfferLanguageRequirement,
    OfferRequirement,
    RedirectHop,
)


@dataclass(frozen=True)
class ProbeRow:
    job_id: str | None
    run_id: int | None
    url: str
    method: str
    status: int | None
    elapsed_ms: int | None
    headers: dict[str, str]
    body_sha256: str | None
    body_path: str | None
    fetched_at: datetime
    error: str | None


@dataclass(frozen=True)
class LanguageRow:
    job_id: str
    language: str | None
    method: str
    confidence: float | None
    detected_at: datetime


@dataclass(frozen=True)
class PlanState:
    job_id: str
    plan_revision: int
    form_hash: str
    probe_fetched_at: datetime
    offer_text_sha256: str


def _dump(value: Sequence[str] | None) -> str | None:
    return json.dumps(list(value)) if value is not None else None


def _load(value: str | None) -> tuple[str, ...] | None:
    return tuple(json.loads(value)) if value is not None else None


class ApplyStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def plan_state(self, job_id: str) -> PlanState | None:
        row = self._conn.execute(
            "SELECT job_id, plan_revision, form_hash, probe_fetched_at, offer_text_sha256 FROM apply_plan WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return PlanState(row["job_id"], row["plan_revision"], row["form_hash"], parse_iso(row["probe_fetched_at"]),
                         row["offer_text_sha256"])

    def jobs_to_build(
        self,
        *,
        limit: int | None = None,
        since: datetime | None = None,
        source: str | None = None,
        survivors_only: bool = True,
        refresh_before: datetime | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[str]:
        clauses = []
        params: list[Any] = []
        if survivors_only:
            clauses.append("j.status IN ('survivor', 'audited')")
        if since is not None:
            clauses.append("j.first_seen_at >= ?")
            params.append(to_iso(since))
        if source is not None:
            clauses.append("j.source = ?")
            params.append(source)
        if job_ids:
            clauses.append(f"j.job_id IN ({', '.join('?' for _ in job_ids)})")
            params.extend(job_ids)
        if refresh_before is None:
            clauses.append("p.job_id IS NULL")
        else:
            clauses.append("(p.job_id IS NULL OR p.probe_fetched_at < ?)")
            params.append(to_iso(refresh_before))
        where = " AND ".join(clauses) if clauses else "1 = 1"
        query = (
            "SELECT j.job_id FROM job j LEFT JOIN apply_plan p ON p.job_id = j.job_id "
            f"WHERE {where} ORDER BY j.first_seen_at, j.job_id"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [r["job_id"] for r in self._conn.execute(query, params)]

    def save_bundle(self, bundle: ApplyPlanBundle, run_id: int | None) -> None:
        plan = bundle.plan
        with self._conn:
            self._conn.execute("DELETE FROM apply_plan WHERE job_id = ?", (plan.job_id,))
            self._conn.execute(
                """
                INSERT INTO apply_plan (job_id, canonical_key, plan_revision, offer_text_sha256, ats_vendor, ats_tenant,
                    ats_external_id, route, route_confidence, route_evidence, apply_url_input, apply_url_final,
                    redirect_chain, http_status, content_type, submit_method, submit_endpoint, submit_enctype,
                    email_recipient, email_subject_template, credential_kind, feasibility, feasibility_reason_code,
                    feasibility_reason_detail, required_field_count, unmapped_required_count, renderer_used,
                    robots_allowed, submission_policy, form_hash, probe_fetched_at, built_at, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.job_id, plan.canonical_key, plan.plan_revision, plan.offer_text_sha256, plan.ats_vendor,
                    plan.ats_tenant, plan.ats_external_id, plan.route.value, plan.route_confidence.value,
                    plan.route_evidence, plan.apply_url_input, plan.apply_url_final,
                    json.dumps([h.model_dump() for h in plan.redirect_chain]), plan.http_status, plan.content_type,
                    plan.submit_method, plan.submit_endpoint, plan.submit_enctype, plan.email_recipient,
                    plan.email_subject_template, plan.credential_kind.value, plan.feasibility.value,
                    plan.feasibility_reason_code.value, plan.feasibility_reason_detail, plan.required_field_count,
                    plan.unmapped_required_count, plan.renderer_used,
                    None if plan.robots_allowed is None else int(plan.robots_allowed), plan.submission_policy.value,
                    plan.form_hash, to_iso(plan.probe_fetched_at), to_iso(plan.built_at), run_id,
                ),
            )
            for f in bundle.fields:
                self._conn.execute(
                    """
                    INSERT INTO apply_field (job_id, ordinal, field_key, label, field_class, field_type, is_required,
                        is_hidden, max_length, min_length, pattern, options, allows_multiple, accept_types, accept_mime,
                        max_bytes, profile_key, profile_key_source, needs_human, semantic_tag, group_label, step_index,
                        evidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f.job_id, f.ordinal, f.field_key, f.label, f.field_class.value, f.field_type.value,
                        int(f.is_required), int(f.is_hidden), f.max_length, f.min_length, f.pattern, _dump(f.options),
                        int(f.allows_multiple), _dump(f.accept_types), _dump(f.accept_mime), f.max_bytes,
                        f.profile_key, f.profile_key_source.value, int(f.needs_human),
                        f.semantic_tag.value if f.semantic_tag else None, f.group_label, f.step_index, f.evidence,
                    ),
                )
            for s in bundle.document_slots:
                self._conn.execute(
                    "INSERT INTO apply_document_slot (job_id, slot, field_key, is_required, accept_types, accept_mime, "
                    "max_bytes, upload_mechanism) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (s.job_id, s.slot.value, s.field_key, int(s.is_required), _dump(s.accept_types),
                     _dump(s.accept_mime), s.max_bytes, s.upload_mechanism.value),
                )
            for o in bundle.obstacles:
                self._conn.execute(
                    "INSERT INTO apply_obstacle (job_id, kind, severity, detector, signal) VALUES (?, ?, ?, ?, ?)",
                    (o.job_id, o.kind.value, o.severity.value, o.detector, o.signal),
                )

    def get_bundle(self, job_id: str) -> ApplyPlanBundle | None:
        row = self._conn.execute("SELECT * FROM apply_plan WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        fields = tuple(self._field(r) for r in self._conn.execute(
            "SELECT * FROM apply_field WHERE job_id = ? ORDER BY ordinal", (job_id,)))
        slots = tuple(self._slot(r) for r in self._conn.execute(
            "SELECT * FROM apply_document_slot WHERE job_id = ? ORDER BY id", (job_id,)))
        obstacles = tuple(self._obstacle(r) for r in self._conn.execute(
            "SELECT * FROM apply_obstacle WHERE job_id = ? ORDER BY id", (job_id,)))
        return ApplyPlanBundle(plan=self._plan(row), fields=fields, document_slots=slots, obstacles=obstacles)

    def iter_bundles(
        self, feasibility: set[Feasibility] | None = None, route: set[ApplyRoute] | None = None
    ) -> Iterator[ApplyPlanBundle]:
        clauses = []
        params: list[Any] = []
        if feasibility:
            clauses.append(f"feasibility IN ({', '.join('?' for _ in feasibility)})")
            params.extend(sorted(f.value for f in feasibility))
        if route:
            clauses.append(f"route IN ({', '.join('?' for _ in route)})")
            params.extend(sorted(r.value for r in route))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        ids = [r["job_id"] for r in self._conn.execute(f"SELECT job_id FROM apply_plan {where} ORDER BY job_id", params)]
        for job_id in ids:
            bundle = self.get_bundle(job_id)
            if bundle is not None:
                yield bundle

    @staticmethod
    def _plan(row: sqlite3.Row) -> ApplyPlan:
        return ApplyPlan(
            job_id=row["job_id"], canonical_key=row["canonical_key"], plan_revision=row["plan_revision"],
            offer_text_sha256=row["offer_text_sha256"], ats_vendor=row["ats_vendor"], ats_tenant=row["ats_tenant"],
            ats_external_id=row["ats_external_id"], route=ApplyRoute(row["route"]),
            route_confidence=Confidence(row["route_confidence"]), route_evidence=row["route_evidence"],
            apply_url_input=row["apply_url_input"], apply_url_final=row["apply_url_final"],
            redirect_chain=tuple(RedirectHop(**h) for h in json.loads(row["redirect_chain"] or "[]")),
            http_status=row["http_status"], content_type=row["content_type"], submit_method=row["submit_method"],
            submit_endpoint=row["submit_endpoint"], submit_enctype=row["submit_enctype"],
            email_recipient=row["email_recipient"], email_subject_template=row["email_subject_template"],
            credential_kind=CredentialKind(row["credential_kind"]), feasibility=Feasibility(row["feasibility"]),
            feasibility_reason_code=ReasonCode(row["feasibility_reason_code"]),
            feasibility_reason_detail=row["feasibility_reason_detail"],
            required_field_count=row["required_field_count"], unmapped_required_count=row["unmapped_required_count"],
            renderer_used=row["renderer_used"],
            robots_allowed=None if row["robots_allowed"] is None else bool(row["robots_allowed"]),
            submission_policy=SubmissionPolicy(row["submission_policy"]), form_hash=row["form_hash"],
            probe_fetched_at=parse_iso(row["probe_fetched_at"]), built_at=parse_iso(row["built_at"]),
        )

    @staticmethod
    def _field(row: sqlite3.Row) -> ApplyField:
        return ApplyField(
            job_id=row["job_id"], ordinal=row["ordinal"], field_key=row["field_key"], label=row["label"],
            field_class=FieldClass(row["field_class"]), field_type=FieldType(row["field_type"]),
            is_required=bool(row["is_required"]), is_hidden=bool(row["is_hidden"]), max_length=row["max_length"],
            min_length=row["min_length"], pattern=row["pattern"], options=_load(row["options"]),
            allows_multiple=bool(row["allows_multiple"]), accept_types=_load(row["accept_types"]),
            accept_mime=_load(row["accept_mime"]), max_bytes=row["max_bytes"], profile_key=row["profile_key"],
            profile_key_source=ProfileKeySource(row["profile_key_source"]), needs_human=bool(row["needs_human"]),
            semantic_tag=SemanticTag(row["semantic_tag"]) if row["semantic_tag"] else None,
            group_label=row["group_label"], step_index=row["step_index"], evidence=row["evidence"],
        )

    @staticmethod
    def _slot(row: sqlite3.Row) -> DocumentSlot:
        return DocumentSlot(
            job_id=row["job_id"], slot=DocumentSlotKind(row["slot"]), field_key=row["field_key"],
            is_required=bool(row["is_required"]), accept_types=_load(row["accept_types"]),
            accept_mime=_load(row["accept_mime"]), max_bytes=row["max_bytes"],
            upload_mechanism=UploadMechanism(row["upload_mechanism"]),
        )

    @staticmethod
    def _obstacle(row: sqlite3.Row) -> Obstacle:
        return Obstacle(job_id=row["job_id"], kind=ObstacleKind(row["kind"]), severity=Severity(row["severity"]),
                        detector=row["detector"], signal=row["signal"])

    def log_probe(self, rows: Sequence[ProbeRow]) -> None:
        with self._conn:
            for r in rows:
                self._conn.execute(
                    "INSERT INTO apply_probe_log (job_id, run_id, url, method, status, elapsed_ms, response_headers, "
                    "body_sha256, body_path, fetched_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r.job_id, r.run_id, r.url, r.method, r.status, r.elapsed_ms, json.dumps(r.headers),
                     r.body_sha256, r.body_path, to_iso(r.fetched_at), r.error),
                )

    def replace_offer_analysis(
        self,
        job_id: str,
        offer_text_sha256: str,
        language: LanguageRow,
        requirements: Sequence[OfferRequirement],
        language_requirements: Sequence[OfferLanguageRequirement],
        facts: Sequence[EmployerFact],
        contacts: Sequence[EmployerContact],
    ) -> None:
        with self._conn:
            for table in ("offer_requirement", "offer_language_requirement", "employer_fact", "employer_contact"):
                self._conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
            self._conn.execute(
                "INSERT OR REPLACE INTO offer_language (job_id, language, method, confidence, detected_at) VALUES (?, ?, ?, ?, ?)",
                (language.job_id, language.language, language.method, language.confidence, to_iso(language.detected_at)),
            )
            for r in requirements:
                self._conn.execute(
                    "INSERT INTO offer_requirement (job_id, offer_text_sha256, skill, level, start_offset, end_offset) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (r.job_id, offer_text_sha256, r.skill, r.level.value, r.start, r.end),
                )
            for r in language_requirements:
                self._conn.execute(
                    "INSERT INTO offer_language_requirement (job_id, offer_text_sha256, language, level, start_offset, "
                    "end_offset) VALUES (?, ?, ?, ?, ?, ?)",
                    (r.job_id, offer_text_sha256, r.language, r.level.value, r.start, r.end),
                )
            for fact in facts:
                self._conn.execute(
                    "INSERT INTO employer_fact (job_id, employer_key, fact, source, source_url) VALUES (?, ?, ?, ?, ?)",
                    (fact.job_id, fact.employer_key, fact.fact, fact.source, fact.source_url),
                )
            for c in contacts:
                self._conn.execute(
                    "INSERT INTO employer_contact (job_id, name, email, phone, source, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (c.job_id, c.name, c.email, c.phone, c.source, to_iso(c.last_seen_at)),
                )

    def offer_language(self, job_id: str) -> LanguageRow | None:
        row = self._conn.execute("SELECT * FROM offer_language WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return LanguageRow(row["job_id"], row["language"], row["method"], row["confidence"], parse_iso(row["detected_at"]))

    def requirements(self, job_id: str) -> list[OfferRequirement]:
        rows = self._conn.execute("SELECT * FROM offer_requirement WHERE job_id = ? ORDER BY start_offset", (job_id,))
        return [OfferRequirement(job_id=r["job_id"], skill=r["skill"], level=r["level"], start=r["start_offset"],
                                 end=r["end_offset"]) for r in rows]

    def language_requirements(self, job_id: str) -> list[OfferLanguageRequirement]:
        rows = self._conn.execute(
            "SELECT * FROM offer_language_requirement WHERE job_id = ? ORDER BY start_offset", (job_id,))
        return [OfferLanguageRequirement(job_id=r["job_id"], language=r["language"], level=r["level"],
                                         start=r["start_offset"], end=r["end_offset"]) for r in rows]

    def employer_facts(self, job_id: str) -> list[EmployerFact]:
        rows = self._conn.execute("SELECT * FROM employer_fact WHERE job_id = ? ORDER BY id", (job_id,))
        return [EmployerFact(job_id=r["job_id"], employer_key=r["employer_key"], fact=r["fact"], source=r["source"],
                             source_url=r["source_url"]) for r in rows]

    def employer_contacts(self, job_id: str) -> list[EmployerContact]:
        rows = self._conn.execute("SELECT * FROM employer_contact WHERE job_id = ? ORDER BY id", (job_id,))
        return [EmployerContact(job_id=r["job_id"], name=r["name"], email=r["email"], phone=r["phone"],
                                source=r["source"], last_seen_at=parse_iso(r["last_seen_at"])) for r in rows]

    def route_feasibility_counts(self) -> list[tuple[str, str, int]]:
        rows = self._conn.execute(
            "SELECT route, feasibility, COUNT(*) AS n FROM apply_plan GROUP BY route, feasibility ORDER BY n DESC")
        return [(r["route"], r["feasibility"], r["n"]) for r in rows]

    def reason_counts(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT feasibility_reason_code AS code, COUNT(*) AS n FROM apply_plan GROUP BY code ORDER BY n DESC")
        return [(r["code"], r["n"]) for r in rows]

    def obstacle_counts(self) -> list[tuple[str, str, int]]:
        rows = self._conn.execute(
            "SELECT kind, detector, COUNT(*) AS n FROM apply_obstacle GROUP BY kind, detector ORDER BY n DESC")
        return [(r["kind"], r["detector"], r["n"]) for r in rows]

    def vendor_route_counts(self) -> list[tuple[str, str, int]]:
        rows = self._conn.execute(
            "SELECT COALESCE(ats_vendor, 'none') AS vendor, route, COUNT(*) AS n FROM apply_plan "
            "GROUP BY vendor, route ORDER BY n DESC")
        return [(r["vendor"], r["route"], r["n"]) for r in rows]

    def unresolved_labels(self, limit: int = 25) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT lower(trim(label)) AS label, COUNT(DISTINCT job_id) AS n FROM apply_field "
            "WHERE profile_key_source = 'unresolved' AND is_required = 1 AND is_hidden = 0 AND label IS NOT NULL "
            "AND field_class IN ('standard', 'custom_question') GROUP BY label ORDER BY n DESC, label LIMIT ?",
            (limit,),
        )
        return [(r["label"], r["n"]) for r in rows]

    def stale_counts(self, older_than: datetime) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM apply_plan WHERE probe_fetched_at < ?", (to_iso(older_than),)).fetchone()
        return int(row["n"])

    def total_plans(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM apply_plan").fetchone()["n"])
