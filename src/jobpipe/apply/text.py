import hashlib
import re
import unicodedata

_NON_WORD = re.compile(r"[^0-9a-z]+")


def fold(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def normalise_label(value: str | None) -> str:
    return _NON_WORD.sub(" ", fold(value).replace("'", " ")).strip()


def contains_phrase(normalised_text: str, phrase: str) -> bool:
    needle = normalise_label(phrase)
    if not needle:
        return False
    return re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])", normalised_text) is not None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
