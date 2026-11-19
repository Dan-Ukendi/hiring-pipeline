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
from docx.enum.text import WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from pypdf import PdfReader

from jobpipe.contract.enums import Severity
from jobpipe.documents import layout
from jobpipe.documents.cv import Block, RenderDoc
from jobpipe.documents.guard import Violation

MAX_BYTES = 300 * 1024
INSTALL_HINT = ("install LibreOffice (Windows: winget install TheDocumentFoundation.LibreOffice; macOS: brew install --cask "
                "libreoffice; Debian/Ubuntu: apt install libreoffice-writer)")
# JOBPIPE_SOFFICE names the soffice executable to use, or "none" to always use the built-in PDF renderer.
SOFFICE_ENV = "JOBPIPE_SOFFICE"
_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
)
_FIXED_STAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
CONVERSION_CACHE: Path | None = None
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class RendererUnavailable(Exception):
    pass


class RenderError(Exception):
    pass


def find_soffice() -> str:
    override = os.environ.get(SOFFICE_ENV, "").strip()
    if override.lower() in {"none", "off", "0", "no"}:
        raise RendererUnavailable(f"LibreOffice is switched off by {SOFFICE_ENV}={override}")
    if override:
        if os.path.isfile(override):
            return override
        raise RendererUnavailable(f"{SOFFICE_ENV} points to {override}, which does not exist")
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in _SOFFICE_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    for pattern in ("/opt/libreoffice*/program/soffice",):
        matches = sorted(Path("/").glob(pattern.lstrip("/"))) if os.name != "nt" else []
        if matches:
            return str(matches[-1])
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
    renderer: str = "libreoffice"


# Schema order of the elements that follow the ones added by hand (Word rejects a file out of order).
_PPR_AFTER_NUMPR = ("w:suppressLineNumbers", "w:pBdr", "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
                    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
                    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap",
                    "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId",
                    "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
_PPR_AFTER_PBDR = _PPR_AFTER_NUMPR[2:]
_RPR_AFTER_SPACING = ("w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd",
                      "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish",
                      "w:oMath")


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def _bullet_numbering(document) -> int:
    """Add a real bullet list (a bullet in the accent colour, hanging indent) and return its numId."""
    numbering = document.part.numbering_part.element
    abstract_id = max([int(a.get(qn("w:abstractNumId"))) for a in numbering.findall(qn("w:abstractNum"))] + [0]) + 1
    num_id = max([int(n.get(qn("w:numId"))) for n in numbering.findall(qn("w:num"))] + [0]) + 1
    indent = round(layout.BULLET_TEXT_INDENT_PT * 20)
    hang = round(layout.BULLET_HANG_PT * 20)
    abstract = parse_xml(
        f'<w:abstractNum xmlns:w="{_W}" w:abstractNumId="{abstract_id}"><w:multiLevelType w:val="singleLevel"/>'
        f'<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="{layout.BULLET}"/>'
        f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{indent}" w:hanging="{hang}"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{layout.FONT_FAMILY}" w:hAnsi="{layout.FONT_FAMILY}" w:cs="{layout.FONT_FAMILY}"/>'
        f'<w:color w:val="{layout.ACCENT}"/></w:rPr></w:lvl></w:abstractNum>')
    first_num = numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)
    numbering.append(parse_xml(f'<w:num xmlns:w="{_W}" w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'))
    return num_id


def _add_run(paragraph, text: str, size: float, bold: bool, colour: str, tracking: float = 0.0) -> None:
    run = paragraph.add_run(text)
    run.font.name = layout.FONT_FAMILY
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(colour)
    if tracking:
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:val"), str(round(tracking * 20)))
        run._r.get_or_add_rPr().insert_element_before(spacing, *_RPR_AFTER_SPACING)


def _rule_below(paragraph) -> None:
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("val", "single"), ("sz", str(round(layout.RULE_WIDTH_PT * 8))), ("space", str(round(layout.RULE_GAP_PT))),
                       ("color", layout.RULE)):
        bottom.set(qn(f"w:{key}"), value)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().insert_element_before(borders, *_PPR_AFTER_PBDR)


def _write_block(document, doc: RenderDoc, block: Block, previous: str | None, bullet_id: int) -> None:
    text_spec = layout.spec(doc.kind, block.style)
    paragraph = document.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(layout.gap_before(doc.kind, previous, block.style))
    fmt.space_after = Pt(0)
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(text_spec.leading)
    fmt.widow_control = True
    if block.style == "heading":
        fmt.keep_with_next = True
        _rule_below(paragraph)
    if block.style == "bullet":
        paragraph._p.get_or_add_pPr().insert_element_before(parse_xml(
            f'<w:numPr xmlns:w="{_W}"><w:ilvl w:val="0"/><w:numId w:val="{bullet_id}"/></w:numPr>'), *_PPR_AFTER_NUMPR)
    for run in block.segments():
        _add_run(paragraph, run.text, text_spec.size, text_spec.bold or run.bold,
                 layout.MUTED_TONE if run.muted else text_spec.colour, text_spec.tracking)
    if block.aside:
        fmt.tab_stops.add_tab_stop(Cm(layout.TEXT_WIDTH_CM), WD_TAB_ALIGNMENT.RIGHT)
        _add_run(paragraph, "\t" + block.aside, text_spec.size, False, layout.MUTED_TONE)


def write_docx(doc: RenderDoc, path: Path) -> None:
    document = Document()
    for section in document.sections:
        section.page_width, section.page_height = Cm(layout.A4_WIDTH_CM), Cm(layout.A4_HEIGHT_CM)
        section.left_margin = section.right_margin = Cm(layout.MARGIN_SIDE_CM)
        section.top_margin, section.bottom_margin = Cm(layout.MARGIN_TOP_CM), Cm(layout.MARGIN_BOTTOM_CM)
        section.header_distance = section.footer_distance = Cm(0.8)
        section.header.is_linked_to_previous = False
        section.footer.is_linked_to_previous = False
    normal = document.styles["Normal"]
    normal.font.name = layout.FONT_FAMILY
    normal.font.size = Pt(layout.BODY.size)
    normal.font.color.rgb = _rgb(layout.INK)
    language = parse_xml(f'<w:lang xmlns:w="{_W}" w:val="{"fr-BE" if doc.language == "fr" else "en-GB"}"/>')
    normal.element.get_or_add_rPr().append(language)
    bullet_id = _bullet_numbering(document)
    previous: str | None = None
    for block in doc.blocks:
        _write_block(document, doc, block, previous, bullet_id)
        previous = block.style
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
    """Write the .docx, then the PDF: with LibreOffice when it is installed, otherwise with the built-in renderer."""
    docx_path = directory / f"{stem}.docx"
    write_docx(doc, docx_path)
    if not make_pdf:
        return RenderedFiles(docx_path, None, None, docx_path.stat().st_size, None)
    if soffice_available():
        pdf_path = convert_to_pdf(docx_path, directory)
        renderer = "libreoffice"
    else:
        from jobpipe.documents.pdf import write_pdf

        pdf_path = write_pdf(doc, directory / f"{stem}.pdf")
        renderer = "builtin"
    return RenderedFiles(docx_path, pdf_path, pdf_page_count(pdf_path), docx_path.stat().st_size, pdf_path.stat().st_size,
                         renderer)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def round_trip_violations(doc: RenderDoc, files: RenderedFiles, max_pages: int | None = 1) -> list[Violation]:
    found: list[Violation] = []

    def fail(detail: str, excerpt: str | None = None) -> None:
        found.append(Violation(layer="render", code="RENDER_PARSE_MISMATCH", severity=Severity.BLOCKING,
                               excerpt=excerpt, detail=detail))

    parsed = [_squash(p) for p in docx_paragraphs(files.docx) if p.strip()]
    expected = [_squash(b.paragraph_text) for b in doc.blocks if b.text.strip()]
    if parsed != expected:
        missing = [e for e in expected if e not in parsed]
        fail("the docx paragraphs do not match the planned order", missing[0][:80] if missing else "order differs")
    plain = "\n".join(parsed)
    if any(ch in plain for ch in ("\ufffd", "Ã©", "Ã¨", "Ã ")):
        fail("replacement characters or mojibake found")
    contact = next((b.text for b in doc.blocks if b.style == "contact"), None)
    if contact and _squash(contact) not in _squash(plain[:600]):
        fail("contact details are not at the top of the document")
    headings = [b.paragraph_text for b in doc.blocks if b.style == "heading"]
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
