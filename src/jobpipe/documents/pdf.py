"""Built-in PDF renderer: the same one-column layout as the .docx, without LibreOffice.

It draws the document blocks with fpdf2 on an A4 page. A TrueType font found on the machine (Arial,
Liberation Sans or DejaVu Sans) is embedded so accents and dashes render and the text stays
extractable by an applicant-tracking system; without one, the PDF core font Helvetica is used.
"""
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

from jobpipe.documents.cv import RenderDoc

_FIXED_STAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
_POINTS_PER_CM = 72 / 2.54
_LINE_FACTOR = 1.25
_BULLET_INDENT_PT = 11.0
# (regular, bold) pairs, first match wins.
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf", "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
)
# Characters the core font cannot encode, mapped to close equivalents.
_CORE_REPLACEMENTS = {"\u2212": "-", "\u2010": "-", "\u2011": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " "}


def find_font() -> tuple[str, str] | None:
    windows = os.environ.get("WINDIR")
    candidates = list(_FONT_CANDIDATES)
    if windows:
        candidates.insert(0, (os.path.join(windows, "Fonts", "arial.ttf"), os.path.join(windows, "Fonts", "arialbd.ttf")))
    for regular, bold in candidates:
        if os.path.isfile(regular) and os.path.isfile(bold):
            return regular, bold
    return None


def _core_safe(text: str) -> str:
    out = []
    for char in text:
        char = _CORE_REPLACEMENTS.get(char, char)
        try:
            char.encode("cp1252")
            out.append(char)
        except UnicodeEncodeError:
            ascii_form = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode("ascii")
            out.append(ascii_form or "?")
    return "".join(out)


class _Writer:
    def __init__(self, body_pt: float, margin_cm: float) -> None:
        margin = margin_cm * _POINTS_PER_CM
        self.pdf = FPDF(orientation="P", unit="pt", format="A4")
        self.pdf.set_margins(margin, margin, margin)
        self.pdf.set_auto_page_break(auto=True, margin=margin)
        self.pdf.set_creation_date(_FIXED_STAMP)
        self.pdf.set_author("jobpipe")
        self.body_pt = body_pt
        font = find_font()
        if font is not None:
            self.pdf.add_font("CV", "", font[0])
            self.pdf.add_font("CV", "B", font[1])
            self.family, self.clean = "CV", (lambda text: text)
        else:
            if hasattr(self.pdf, "core_fonts_encoding"):
                self.pdf.core_fonts_encoding = "windows-1252"
            self.family, self.clean = "Helvetica", _core_safe
        self.pdf.add_page()

    def paragraph(self, text: str, size: float, bold: bool = False, before: float = 0.0, after: float = 2.0,
                  bullet: str | None = None) -> None:
        pdf = self.pdf
        if before:
            pdf.ln(before)
        pdf.set_font(self.family, "B" if bold else "", size)
        height = size * _LINE_FACTOR
        if bullet:
            pdf.cell(w=_BULLET_INDENT_PT, h=height, text=self.clean(bullet.strip()))
            pdf.multi_cell(w=pdf.epw - _BULLET_INDENT_PT, h=height, text=self.clean(text), align="L", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.multi_cell(w=0, h=height, text=self.clean(text), align="L", new_x="LMARGIN", new_y="NEXT")
        if after:
            pdf.ln(after)


def write_pdf(doc: RenderDoc, path: Path, body_pt: float, margin_cm: float, bullet_prefix: str) -> Path:
    writer = _Writer(body_pt, margin_cm)
    letter = doc.kind == "letter"
    for block in doc.blocks:
        if not block.text.strip():
            continue
        if block.style == "name":
            writer.paragraph(block.text, 16, bold=True)
        elif block.style == "title":
            writer.paragraph(block.text, 12, bold=True)
        elif block.style == "heading":
            writer.paragraph(block.text, 11.5, bold=True, before=6, after=3)
        elif block.style == "entry":
            writer.paragraph(block.text, body_pt, bold=True)
        elif block.style == "bullet":
            writer.paragraph(block.text, body_pt, bullet=bullet_prefix)
        else:
            writer.paragraph(block.text, body_pt, after=4.0 if letter else 2.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer.pdf.output(str(path))
    return path
