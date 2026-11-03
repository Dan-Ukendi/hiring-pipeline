import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from jobpipe.contract.clock import parse_iso, to_iso
from jobpipe.contract.enums import ApprovedBy, PackageStatus, Tier
from jobpipe.contract.models import PackageManifest


class ApprovalError(Exception):
    pass


@dataclass(frozen=True)
class PackageRow:
    package_id: str
    job_id: str
    plan_revision: int
    run_id: int
    status: PackageStatus
    hold_reason: str | None
    tier: Tier
    approved_by: ApprovedBy | None
    approved_at: datetime | None
    bundle_sha256: str
    offer_text_sha256: str
    guard_blocking: int
    guard_l4_unsupported: int
    manifest_path: str
    created_at: datetime


def _row(row: sqlite3.Row) -> PackageRow:
    return PackageRow(
        package_id=row["package_id"], job_id=row["job_id"], plan_revision=row["plan_revision"], run_id=row["run_id"],
        status=PackageStatus(row["status"]), hold_reason=row["hold_reason"], tier=Tier(row["tier"]),
        approved_by=ApprovedBy(row["approved_by"]) if row["approved_by"] else None,
        approved_at=parse_iso(row["approved_at"]) if row["approved_at"] else None,
        bundle_sha256=row["bundle_sha256"], offer_text_sha256=row["offer_text_sha256"],
        guard_blocking=row["guard_blocking"], guard_l4_unsupported=row["guard_l4_unsupported"],
        manifest_path=row["manifest_path"], created_at=parse_iso(row["created_at"]),
    )


class DocumentsStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def register_profile(self, version: str, content_hash: str, schema_version: int, loaded_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO profile_versions (profile_version, content_hash, schema_version, loaded_at) VALUES (?, ?, ?, ?)",
                (version, content_hash, schema_version, to_iso(loaded_at)),
            )

    def save_package(
        self,
        manifest: PackageManifest,
        *,
        profile_version: str,
        model_name: str | None,
        prompt_version: str,
        hold_reason: str | None,
        started_at: datetime,
        finished_at: datetime,
        llm_calls: int,
        manifest_path: str,
        selection: dict[str, str],
        documents: list[dict],
        answers: list[dict],
        findings: list[dict],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE application_packages SET status = 'superseded' WHERE job_id = ? AND status IN ('draft', 'ready', 'awaiting_review', 'held')",
                (manifest.job_id,),
            )
            self._conn.execute(
                "INSERT INTO generation_runs (package_id, job_id, plan_revision, run_id, profile_version, model_name, "
                "prompt_version, doc_language, language_source, tier, started_at, finished_at, status, hold_reason, llm_calls) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (manifest.package_id, manifest.job_id, manifest.plan_revision, manifest.run_id, profile_version, model_name,
                 prompt_version, manifest.language, manifest.language_source.value, manifest.tier.value, to_iso(started_at),
                 to_iso(finished_at), manifest.status.value, hold_reason, llm_calls),
            )
            self._conn.execute(
                "INSERT INTO selection_plans (package_id, plan_json, gap_json, prescore_json, llm_reordered) VALUES (?, ?, ?, ?, ?)",
                (manifest.package_id, selection["plan"], selection["gaps"], selection["prescore"], int(selection["llm"] == "1")),
            )
            for d in documents:
                self._conn.execute(
                    "INSERT INTO documents (document_id, package_id, role, language, format, path, sha256, bytes, mime_type, "
                    "word_count, page_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (d["document_id"], manifest.package_id, d["role"], manifest.language, d["format"], d["path"], d["sha256"],
                     d["bytes"], d["mime_type"], d.get("word_count"), d.get("page_count")),
                )
            for a in answers:
                self._conn.execute(
                    "INSERT INTO question_answers (package_id, field_key, label, category, answer_value, source, max_length, "
                    "requires_human, hold_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (manifest.package_id, a["field_key"], a["label"], a["category"], json.dumps(a["value"]), a["source"],
                     a.get("max_length"), int(a["requires_human"]), a.get("hold_reason")),
                )
            for f in findings:
                self._conn.execute(
                    "INSERT INTO guard_findings (package_id, scope, layer, code, severity, span_start, span_end, excerpt, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (manifest.package_id, f["scope"], f["layer"], f["code"], f["severity"], f.get("span_start"),
                     f.get("span_end"), f.get("excerpt"), f.get("detail")),
                )
            self._conn.execute(
                "INSERT INTO application_packages (package_id, job_id, plan_revision, run_id, status, hold_reason, tier, "
                "approved_by, approved_at, bundle_sha256, offer_text_sha256, guard_blocking, guard_l4_unsupported, "
                "manifest_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (manifest.package_id, manifest.job_id, manifest.plan_revision, manifest.run_id, manifest.status.value,
                 hold_reason, manifest.tier.value, manifest.approved_by.value if manifest.approved_by else None,
                 to_iso(finished_at) if manifest.approved_by else None, manifest.bundle_sha256, manifest.offer_text_sha256,
                 manifest.guard_summary.blocking, manifest.guard_summary.l4_unsupported, manifest_path, to_iso(manifest.created_at)),
            )
            if manifest.approved_by is ApprovedBy.POLICY:
                self._conn.execute(
                    "INSERT INTO package_approval_event (package_id, event, actor, at) VALUES (?, 'approved', 'policy', ?)",
                    (manifest.package_id, to_iso(finished_at)),
                )

    def get(self, package_id: str) -> PackageRow | None:
        row = self._conn.execute("SELECT * FROM application_packages WHERE package_id = ?", (package_id,)).fetchone()
        return _row(row) if row else None

    def active_for_job(self, job_id: str) -> PackageRow | None:
        row = self._conn.execute(
            "SELECT * FROM application_packages WHERE job_id = ? AND status IN ('ready', 'awaiting_review', 'held', 'draft') "
            "ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
        return _row(row) if row else None

    def list_by_status(self, status: PackageStatus) -> list[PackageRow]:
        rows = self._conn.execute("SELECT * FROM application_packages WHERE status = ? ORDER BY tier, created_at", (status.value,))
        return [_row(r) for r in rows]

    def approve(self, package_id: str, actor: str, at: datetime) -> PackageRow:
        row = self.get(package_id)
        if row is None:
            raise ApprovalError(f"unknown package {package_id}")
        if row.status is PackageStatus.HELD:
            raise ApprovalError("a held package cannot be approved; generate it again after fixing the cause")
        if row.status is not PackageStatus.AWAITING_REVIEW:
            raise ApprovalError(f"package is {row.status.value}, only awaiting_review packages can be approved")
        with self._conn:
            self._conn.execute(
                "UPDATE application_packages SET status = 'ready', approved_by = 'human', approved_at = ? WHERE package_id = ?",
                (to_iso(at), package_id),
            )
            self._conn.execute(
                "UPDATE generation_runs SET status = 'ready' WHERE package_id = ?", (package_id,))
            self._conn.execute(
                "INSERT INTO package_approval_event (package_id, event, actor, at) VALUES (?, 'approved', ?, ?)",
                (package_id, actor, to_iso(at)),
            )
        approved = self.get(package_id)
        assert approved is not None
        return approved

    def reject(self, package_id: str, actor: str, at: datetime) -> None:
        row = self.get(package_id)
        if row is None or row.status is not PackageStatus.AWAITING_REVIEW:
            raise ApprovalError("only awaiting_review packages can be rejected")
        with self._conn:
            self._conn.execute("UPDATE application_packages SET status = 'held', hold_reason = 'rejected_by_reviewer' WHERE package_id = ?", (package_id,))
            self._conn.execute("UPDATE generation_runs SET status = 'held', hold_reason = 'rejected_by_reviewer' WHERE package_id = ?", (package_id,))
            self._conn.execute(
                "INSERT INTO package_approval_event (package_id, event, actor, at) VALUES (?, 'rejected', ?, ?)",
                (package_id, actor, to_iso(at)),
            )

    def approval_events(self, package_id: str) -> list[tuple[str, str]]:
        rows = self._conn.execute("SELECT event, actor FROM package_approval_event WHERE package_id = ? ORDER BY id", (package_id,))
        return [(r["event"], r["actor"]) for r in rows]

    def findings(self, package_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM guard_findings WHERE package_id = ? ORDER BY id", (package_id,)))

    def tier_signals(self, job_id: str) -> tuple[bool, bool, str | None]:
        row = self._conn.execute("SELECT status FROM job WHERE job_id = ?", (job_id,)).fetchone()
        survivor = bool(row) and row["status"] in ("survivor", "audited")
        borderline = self._conn.execute(
            "SELECT 1 FROM filter_result WHERE job_id = ? AND filter = 'seniority' AND outcome = 'borderline' LIMIT 1",
            (job_id,)).fetchone() is not None
        audit = self._conn.execute(
            "SELECT verdict FROM audit WHERE job_id = ? AND role = 'primary' ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        return survivor, borderline, audit["verdict"] if audit else None

    def eligible_job_ids(self, limit: int | None, job_ids: list[str] | None = None) -> list[str]:
        params: list[object] = []
        clause = ""
        if job_ids:
            clause = f" AND p.job_id IN ({', '.join('?' for _ in job_ids)})"
            params.extend(job_ids)
        query = (
            "SELECT p.job_id FROM apply_plan p WHERE p.feasibility != 'blocked'" + clause +
            " AND NOT EXISTS (SELECT 1 FROM application_packages ap WHERE ap.job_id = p.job_id AND ap.status != 'superseded' "
            "AND ap.plan_revision = p.plan_revision AND ap.offer_text_sha256 = p.offer_text_sha256) ORDER BY p.job_id"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [r["job_id"] for r in self._conn.execute(query, params)]
