import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class PurgeReport:
    removed: list[Path] = field(default_factory=list)
    kept_sent: int = 0
    freed_bytes: int = 0
    dry_run: bool = True


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def backup_database(connection: sqlite3.Connection, directory: Path, now: datetime, keep: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"jobpipe-{now.strftime('%Y%m%d-%H%M%S')}.db"
    destination = sqlite3.connect(target)
    try:
        connection.backup(destination)
    finally:
        destination.close()
    backups = sorted(directory.glob("jobpipe-*.db"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()
    return target


def purge_documents(runs_root: Path, protected: set[Path], now: datetime, older_than_days: int, dry_run: bool) -> PurgeReport:
    report = PurgeReport(dry_run=dry_run)
    cutoff = now - timedelta(days=older_than_days)
    if not runs_root.exists():
        return report
    for job_dir in runs_root.iterdir():
        if not job_dir.is_dir():
            continue
        for run_dir in job_dir.iterdir():
            if not run_dir.is_dir():
                continue
            if run_dir.resolve() in protected:
                report.kept_sent += 1
                continue
            modified = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=cutoff.tzinfo)
            if modified > cutoff:
                continue
            report.freed_bytes += directory_size(run_dir)
            report.removed.append(run_dir)
            if not dry_run:
                import shutil

                shutil.rmtree(run_dir)
    return report


def purge_archive(archive_root: Path, now: datetime, retention_days: int, dry_run: bool) -> PurgeReport:
    report = PurgeReport(dry_run=dry_run)
    cutoff = (now - timedelta(days=retention_days)).timestamp()
    if not archive_root.exists():
        return report
    for file in archive_root.rglob("*"):
        if file.is_file() and file.stat().st_mtime < cutoff:
            report.freed_bytes += file.stat().st_size
            report.removed.append(file)
            if not dry_run:
                file.unlink()
    return report
