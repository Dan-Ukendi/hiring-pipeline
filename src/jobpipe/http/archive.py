import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ArchivedFile:
    sha256: str
    path: Path
    relative_path: str
    bytes: int


def extension_for(content_type: str | None) -> str:
    lowered = (content_type or "").lower()
    if "json" in lowered:
        return "json"
    if "html" in lowered:
        return "html"
    return "bin"


class Archive:
    def __init__(self, root: Path, base: Path) -> None:
        self.root = root
        self.base = base

    def write(self, content: bytes, content_type: str | None, fetched_at: datetime) -> ArchivedFile:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.root / fetched_at.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.{extension_for(content_type)}"
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return ArchivedFile(
            sha256=digest,
            path=target,
            relative_path=self._relative(target),
            bytes=len(content),
        )

    def _relative(self, target: Path) -> str:
        try:
            return target.relative_to(self.base).as_posix()
        except ValueError:
            return target.as_posix()
