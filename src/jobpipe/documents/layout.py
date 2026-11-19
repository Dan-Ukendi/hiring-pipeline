"""Design tokens of the CV and the cover letter, shared by the .docx writer and the built-in PDF writer.

Every size, colour and space of the layout is defined here and nowhere else, so that the Word file,
the PDF made by LibreOffice from it and the PDF made by the built-in renderer share one layout.

The design is a sober one-column page: a large name, a title in one restrained accent colour, a
muted contact line, uppercase letter-spaced section headings with a thin rule, entries with the role
in bold and the dates right-aligned, and real bullets with a hanging indent. It stays ATS-safe: one
column, no table, no text box, no image, and nothing in the page header or footer.

Font: the Word file asks for Calibri (installed with Office; LibreOffice substitutes Carlito for it,
which has the same metrics). The built-in renderer embeds the bundled Carlito, so the three outputs
break their lines at the same places.
"""
from dataclasses import dataclass
from pathlib import Path

FONT_FAMILY = "Calibri"
FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FILES = (FONT_DIR / "Carlito-Regular.ttf", FONT_DIR / "Carlito-Bold.ttf")

# Colours as RRGGBB.
INK = "1F2933"
ACCENT = "1F3A5F"
MUTED = "5B6573"
RULE = "C5CDD8"

A4_WIDTH_CM, A4_HEIGHT_CM = 21.0, 29.7
MARGIN_SIDE_CM = 1.8
MARGIN_TOP_CM = 1.5
MARGIN_BOTTOM_CM = 1.3
TEXT_WIDTH_CM = A4_WIDTH_CM - 2 * MARGIN_SIDE_CM

BULLET = "•"
BULLET_TEXT_INDENT_PT = 14.0   # where the text of a bullet starts
BULLET_HANG_PT = 11.0          # distance from the bullet glyph to the text
RULE_GAP_PT = 2.5              # between the heading text and its rule
RULE_WIDTH_PT = 0.5
DATE_GAP_MIN_PT = 10.0         # minimum space between an entry's text and its right-aligned dates
POINTS_PER_CM = 72 / 2.54


@dataclass(frozen=True)
class TextSpec:
    size: float
    leading: float
    bold: bool = False
    colour: str = INK
    tracking: float = 0.0   # extra space between letters, in points; keep it <= 0.7: wider and PDF text extractors split the word into letters
    ruled: bool = False     # thin rule under the paragraph


BODY = TextSpec(10.0, 12.3)
CV_SPECS = {
    "name": TextSpec(23, 26, bold=True),
    "title": TextSpec(12, 14.5, colour=ACCENT),
    "contact": TextSpec(9, 11.8, colour=MUTED),
    "heading": TextSpec(9, 11.5, bold=True, colour=ACCENT, tracking=0.5, ruled=True),
    "entry": BODY,
    "line": BODY,
    "bullet": BODY,
    "body": BODY,
    "signature": BODY,
}
LETTER_SPECS = {
    **CV_SPECS,
    "name": TextSpec(20, 24, bold=True),
    "body": TextSpec(11, 15.2),
    "signature": TextSpec(11, 15.2, bold=True),
}
# Muted tone for the dates and for any run flagged "muted" (same size as the paragraph).
MUTED_TONE = MUTED


def spec(kind: str, style: str) -> TextSpec:
    table = LETTER_SPECS if kind == "letter" else CV_SPECS
    return table.get(style, BODY)


def gap_before(kind: str, previous: str | None, style: str) -> float:
    """Space above a paragraph, in points, from the style of the paragraph above it."""
    if previous is None:
        return 0.0
    if kind == "letter":
        if style == "contact":
            return 3.0
        if style == "body":
            return 26.0 if previous == "contact" else 10.0
        if style == "signature":
            return 4.0
        return 6.0
    if style == "title":
        return 2.0
    if style == "contact":
        return 6.0 if previous == "title" else 1.5
    if style == "heading":
        return 14.0 if previous in {"contact", "title", "name"} else 10.5
    if style == "entry":
        return 5.0 if previous == "heading" else 6.5
    if style == "line":
        if previous == "heading":
            return 5.0
        return 1.0 if previous == "entry" else 2.0
    if style == "bullet":
        if previous == "heading":
            return 5.0
        return 2.0 if previous in {"entry", "line"} else 1.5
    return 3.0
