import hashlib
import re
import unicodedata
from collections.abc import Iterable

from jobpipe.config import Taxonomy

LEGAL_SUFFIXES = frozenset(
    {
        "nv", "sa", "bv", "bvba", "sprl", "srl", "cvba", "vzw", "asbl", "gmbh", "ag", "ltd", "limited", "llc",
        "inc", "incorporated", "plc", "corp", "corporation", "sas", "sarl", "ab", "oy", "spa", "kg", "ug", "se", "co",
    }
)

BRUSSELS = "brussels"

_GENDER_MARK = re.compile(r"[\(\[]\s*[mfwhdx*]\s*(?:[/|]\s*[mfwhdx*]\s*)+[\)\]]|\b[mfwhd]\s*/\s*[mfwhdx]\s*(?:/\s*[mfwhdx]\s*)?(?=\W|$)")
_JOINED = re.compile(r"[.'’]")
_SEPARATORS = re.compile(r"[^0-9a-z]+")


def fold(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _tokens(value: str) -> list[str]:
    return _SEPARATORS.sub(" ", _JOINED.sub("", fold(value))).split()


def normalise_company(name: str | None) -> str:
    tokens = _tokens(name or "")
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalise_title(title: str | None) -> str:
    return " ".join(_tokens(_GENDER_MARK.sub(" ", fold(title))))


class CanonicalKeyer:
    def __init__(self, city_aliases: Iterable[str] = ()) -> None:
        self._brussels = frozenset(" ".join(_tokens(a)) for a in city_aliases) | {BRUSSELS}

    @classmethod
    def from_taxonomy(cls, taxonomy: Taxonomy) -> "CanonicalKeyer":
        names = [*taxonomy.region.markers]
        for commune in taxonomy.communes:
            if not commune.ambiguous:
                names.extend([commune.name, *commune.aliases])
        return cls(names)

    def _normalised(self, value: str) -> str:
        normalised = " ".join(_tokens(value))
        return BRUSSELS if normalised in self._brussels else normalised

    def city(self, city: str | None, location_raw: str | None) -> str:
        if city:
            return self._normalised(city)
        segments = [self._normalised(s) for s in re.split(r"[,;|]", location_raw or "")]
        return BRUSSELS if BRUSSELS in segments else segments[0]

    def key(self, company: str | None, title: str | None, city: str | None, location_raw: str | None = None) -> str:
        material = "|".join((normalise_company(company), normalise_title(title), self.city(city, location_raw)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


DEFAULT_KEYER = CanonicalKeyer()
