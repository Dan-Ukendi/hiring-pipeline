import html
import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser

_BLOCK_SELECTOR = "p,div,h1,h2,h3,h4,h5,h6,ul,ol,tr,table,section,article,blockquote"
_SPACES = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalise(value: str | None) -> str:
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(folded.split())


def unescape_html(value: str) -> str:
    return html.unescape(value)


def html_to_text(markup: str) -> str:
    tree = LexborHTMLParser(markup)
    for node in tree.css("script,style"):
        node.decompose()
    for node in tree.css("br"):
        node.replace_with("\n")
    for node in tree.css(_BLOCK_SELECTOR):
        node.insert_before("\n")
        node.insert_after("\n")
    for node in tree.css("li"):
        node.insert_before("\n- ")
    body = tree.body
    raw = body.text(deep=True, separator="", strip=False) if body is not None else ""
    lines = (_SPACES.sub(" ", line.replace("\xa0", " ")).strip() for line in raw.splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
