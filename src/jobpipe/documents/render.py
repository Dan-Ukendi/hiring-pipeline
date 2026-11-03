import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from pypdf import PdfReader

from jobpipe.contract.enums import Severity
from jobpipe.documents.cv import RenderDoc
from jobpipe.documents.guard import Violation

FONT = "Arial"
BODY_PT = 10.5
MARGIN_CM = 1.8
MAX_BYTES = 300 * 1024
INSTALL_HINT = "install LibreOffice (Windows: winget install TheDocumentFoundation.LibreOffice; Debian: apt install libreoffice-writer)"
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
_BULLET_PREFIX = "• "
_FIXED_STAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
CONVERSION_CACHE: Path | None = None


class RendererUnavailable(Exception):
    pass


class RenderError(Exception):
    pass


def find_soffice() -> str:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in _WINDOWS_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise RendererUnavailable(f"soffice was not found; {INSTALL_HINT}")


def soffice_available() -> bool:
    try:
        find_soffice()
    except RendererUnavailable:
        return False
    return True


@dataclass(frozen=True)
class RenderedFiles:
    docx: Path
    pdf: Path | None
    page_count: int | None
    bytes_docx: int
    bytes_pdf: int | None


def _style(paragraph, size: float, bold: bool = False, align=None, space_after: float = 2.0) -> None:
    for run in paragraph.runs:
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
    fmt = paragraph.paragraph_format
    fmt.space_after = Pt(space_after)
    fmt.space_before = Pt(0)
    fmt.line_spacing = 1.08
    if align is not None:
        paragraph.alignment = align


def write_docx(doc: RenderDoc, path: Path) -> None:
    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Cm(MARGIN_CM)
        section.top_margin = section.bottom_margin = Cm(MARGIN_CM)
        section.header.is_linked_to_previous = False
        section.footer.is_linked_to_previous = False
    normal = document.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY_PT)
    for block in doc.blocks:
        text = _BULLET_PREFIX + block.text if block.style == "bullet" else block.text
        paragraph = document.add_paragraph(text)
        if block.style == "name":
            _style(paragraph, 16, True)
        elif block.style == "title":
            _style(paragraph, 12, True)
        elif block.style == "heading":
            _style(paragraph, 11.5, True, space_after=3.0)
            paragraph.paragraph_format.space_before = Pt(6)
        elif block.style == "entry":
            _style(paragraph, BODY_PT, True)
        elif block.style == "signature":
            _style(paragraph, BODY_PT, False, WD_ALIGN_PARAGRAPH.LEFT)
        else:
            _style(paragraph, BODY_PT, space_after=4.0 if doc.kind == "letter" else 2.0)
    properties = document.core_properties
    properties.author = properties.last_modified_by = "jobpipe"
    properties.created = properties.modified = properties.last_printed = _FIXED_STAMP
    properties.title = properties.subject = properties.comments = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def _content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            digest.update(name.encode("utf-8"))
            digest.update(archive.read(name))
    return digest.hexdigest()


def convert_to_pdf(docx_path: Path, out_dir: Path, timeout: float = 120.0) -> Path:
    soffice = find_soffice()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (docx_path.stem + ".pdf")
    cached = None
    if CONVERSION_CACHE is not None:
        cached = CONVERSION_CACHE / (_content_digest(docx_path) + ".pdf")
        if cached.is_file():
            shutil.copyfile(cached, target)
            return target
    with tempfile.TemporaryDirectory(prefix="jobpipe-lo-") as profile_dir:
        command = [soffice, f"-env:UserInstallation={Path(profile_dir).as_uri()}", "--headless", "--convert-to", "pdf",
                   "--outdir", str(out_dir), str(docx_path)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"PDF conversion timed out after {timeout:.0f}s") from exc
    if completed.returncode != 0 or not target.is_file():
        raise RenderError(f"PDF conversion failed: {completed.stderr.strip() or completed.stdout.strip()}")
    if cached is not None:
        shutil.copyfile(target, cached)
    return target


def pdf_page_count(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def docx_paragraphs(path: Path) -> list[str]:
    return [p.text for p in Document(str(path)).paragraphs]


def render_files(doc: RenderDoc, directory: Path, stem: str, make_pdf: bool = True) -> RenderedFiles:
    docx_path = directory / f"{stem}.docx"
    write_docx(doc, docx_path)
    if not make_pdf:
        return RenderedFiles(docx_path, None, None, docx_path.stat().st_size, None)
    pdf_path = convert_to_pdf(docx_path, directory)
    return RenderedFiles(docx_path, pdf_path, pdf_page_count(pdf_path), docx_path.stat().st_size, pdf_path.stat().st_size)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def round_trip_violations(doc: RenderDoc, files: RenderedFiles, max_pages: int | None = 1) -> list[Violation]:
    found: list[Violation] = []

    def fail(detail: str, excerpt: str | None = None) -> None:
        found.append(Violation(layer="render", code="RENDER_PARSE_MISMATCH", severity=Severity.BLOCKING,
                               excerpt=excerpt, detail=detail))

    parsed = [_squash(p) for p in docx_paragraphs(files.docx) if p.strip()]
    expected = [_squash((_BULLET_PREFIX + b.text) if b.style == "bullet" else b.text) for b in doc.blocks if b.text.strip()]
    if parsed != expected:
        missing = [e for e in expected if e not in parsed]
        fail("the docx paragraphs do not match the planned order", missing[0][:80] if missing else "order differs")
    plain = "\n".join(parsed)
    if any(ch in plain for ch in ("\ufffd", "Ã©", "Ã¨", "Ã ")):
        fail("replacement characters or mojibake found")
    contact = next((b.text for b in doc.blocks if b.style == "contact"), None)
    if contact and _squash(contact) not in _squash(plain[:600]):
        fail("contact details are not at the top of the document")
    headings = doc.headings()
    positions = [plain.find(_squash(h)) for h in headings]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        fail("section headings are missing or out of order")
    if files.bytes_docx > MAX_BYTES or (files.bytes_pdf or 0) > MAX_BYTES:
        fail("rendered file exceeds 300 KB")
    if files.pdf is not None:
        if max_pages is not None and (files.page_count or 0) > max_pages:
            fail(f"document has {files.page_count} pages, limit is {max_pages}")
        text = _squash(pdf_text(files.pdf))
        for heading in headings:
            if _squash(heading) not in text:
                fail("a section heading is not extractable from the PDF", heading)
                break
    return found
