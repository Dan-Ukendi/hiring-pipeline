"""Built-in PDF renderer: the same one-column layout as the .docx, without LibreOffice.

It draws the document blocks with fpdf2 on an A4 page, from the design tokens of ``layout``. The
bundled Carlito font (open licence, same metrics as Calibri) is embedded, so accents, dashes and
non-breaking spaces render and the text stays real, selectable text in reading order for an
applicant-tracking system. If the bundled font is missing, a TrueType font found on the machine is
used, and as a last resort the core font Helvetica.
"""
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

from jobpipe.documents import layout
from jobpipe.documents.cv import Block, RenderDoc

_FIXED_STAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)
# (regular, bold) pairs tried when the bundled font is missing, first match wins.
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
    regular, bold = layout.FONT_FILES
    if regular.is_file() and bold.is_file():
        return str(regular), str(bold)
    windows = os.environ.get("WINDIR")
    candidates = list(_FONT_CANDIDATES)
    if windows:
        candidates.insert(0, (os.path.join(windows, "Fonts", "arial.ttf"), os.path.join(windows, "Fonts", "arialbd.ttf")))
    for regular_path, bold_path in candidates:
        if os.path.isfile(regular_path) and os.path.isfile(bold_path):
            return regular_path, bold_path
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


def _rgb(value: str) -> tuple[int, int, int]:
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class _Writer:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        pdf = self.pdf = FPDF(orientation="P", unit="pt", format="A4")
        side = layout.MARGIN_SIDE_CM * layout.POINTS_PER_CM
        pdf.set_margins(side, layout.MARGIN_TOP_CM * layout.POINTS_PER_CM, side)
        pdf.set_auto_page_break(auto=True, margin=layout.MARGIN_BOTTOM_CM * layout.POINTS_PER_CM)
        pdf.set_creation_date(_FIXED_STAMP)
        pdf.set_author("jobpipe")
        pdf.set_compression(True)
        pdf.c_margin = 0.0  # no cell padding: text starts exactly at the margin, as in the .docx
        font = find_font()
        if font is not None:
            pdf.add_font("CV", "", font[0])
            pdf.add_font("CV", "B", font[1])
            self.family, self.clean = "CV", (lambda text: text)
        else:
            if hasattr(pdf, "core_fonts_encoding"):
                pdf.core_fonts_encoding = "windows-1252"
            self.family, self.clean = "Helvetica", _core_safe
        pdf.add_page()
        self.margin = side
        self.previous: str | None = None

    @property
    def right(self) -> float:
        return self.pdf.w - self.pdf.r_margin

    def _font(self, spec: layout.TextSpec, bold: bool, colour: str) -> None:
        self.pdf.set_font(self.family, "B" if bold else "", spec.size)
        self.pdf.set_text_color(*_rgb(colour))
        self.pdf.set_char_spacing(spec.tracking)

    def _flow(self, block: Block, spec: layout.TextSpec) -> None:
        """Write the runs of the block from the current position, wrapping at the right margin."""
        for run in block.segments():
            self._font(spec, spec.bold or run.bold, layout.MUTED_TONE if run.muted else spec.colour)
            self.pdf.write(h=spec.leading, text=self.clean(run.text))

    def _aside(self, text: str, spec: layout.TextSpec) -> None:
        """Dates flush right: on the same line if they fit, else alone on the next line (as a Word right tab does)."""
        pdf = self.pdf
        self._font(spec, False, layout.MUTED_TONE)
        text = self.clean(text)
        width = pdf.get_string_width(text)
        if pdf.get_x() + layout.DATE_GAP_MIN_PT + width > self.right:
            pdf.ln(spec.leading)
        pdf.set_x(self.right - width)
        pdf.write(h=spec.leading, text=text)

    def block(self, block: Block) -> None:
        pdf = self.pdf
        spec = layout.spec(self.kind, block.style)
        pdf.ln(layout.gap_before(self.kind, self.previous, block.style))
        pdf.set_x(self.margin)
        if block.style == "bullet":
            indent = layout.BULLET_TEXT_INDENT_PT
            self._font(spec, False, layout.ACCENT)
            pdf.cell(w=indent, h=spec.leading, text=self.clean(layout.BULLET))
            pdf.l_margin = self.margin + indent
            pdf.set_x(pdf.l_margin)
            self._flow(block, spec)
            pdf.l_margin = self.margin
        else:
            self._flow(block, spec)
        if block.aside:
            self._aside(block.aside, spec)
        pdf.ln(spec.leading)
        pdf.set_char_spacing(0)
        if spec.ruled:
            pdf.set_draw_color(*_rgb(layout.RULE))
            pdf.set_line_width(layout.RULE_WIDTH_PT)
            y = pdf.get_y() + layout.RULE_GAP_PT
            pdf.line(self.margin, y, self.right, y)
            pdf.set_y(y + layout.RULE_WIDTH_PT)
        pdf.set_x(self.margin)
        self.previous = block.style


def write_pdf(doc: RenderDoc, path: Path) -> Path:
    writer = _Writer(doc.kind)
    for block in doc.blocks:
        if block.text.strip():
            writer.block(block)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer.pdf.output(str(path))
    return path
