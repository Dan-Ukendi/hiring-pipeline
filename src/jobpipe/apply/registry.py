import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from jobpipe.apply.rules import ApplyRules


@dataclass(frozen=True)
class Fingerprint:
    vendor: str
    kind: str
    tenant: str | None
    external_id: str | None


class AtsRegistry:
    def __init__(self, rules: ApplyRules) -> None:
        self._vendors = [
            (vendor, [re.compile(p, re.IGNORECASE) for p in vendor.patterns]) for vendor in rules.registry.vendor
        ]
        self._aggregators = tuple(h.lower() for h in rules.registry.aggregators.hosts)

    @staticmethod
    def _key(url: str) -> str | None:
        parts = urlsplit(url)
        if not parts.hostname:
            return None
        path = parts.path or "/"
        query = f"?{parts.query}" if parts.query else ""
        return f"{parts.hostname.lower()}{path}{query}"

    def fingerprint(self, url: str | None) -> Fingerprint | None:
        if not url:
            return None
        key = self._key(url)
        if key is None:
            return None
        for vendor, patterns in self._vendors:
            for pattern in patterns:
                match = pattern.search(key)
                if match:
                    groups = match.groupdict()
                    return Fingerprint(vendor.name, vendor.kind, groups.get("tenant"), groups.get("external_id"))
        return None

    def is_aggregator(self, url: str | None) -> bool:
        if not url:
            return False
        host = (urlsplit(url).hostname or "").lower()
        return any(host == h or host.endswith(f".{h}") for h in self._aggregators)
