import hashlib
import re
from urllib.parse import urlsplit

from jobpipe.canonical import normalise_company
from jobpipe.contract.enums import ApplyRoute

_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov"})
_SPACES = re.compile(r"\s+")


def registrable_domain(host: str) -> str:
    labels = [part for part in host.lower().strip(".").split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def degraded_employer_key(site_url: str | None, company_name: str | None) -> str:
    if site_url:
        host = urlsplit(site_url).hostname
        if host:
            return f"domain:{registrable_domain(host)}"
    name = normalise_company(company_name)
    if not name:
        raise ValueError("an employer key needs a site URL or a company name")
    return f"name:{_SPACES.sub('-', name)}"


def ats_employer_key(vendor: str, tenant: str) -> str:
    if not vendor or not tenant:
        raise ValueError("an ATS employer key needs a vendor and a tenant")
    return f"ats:{vendor.lower()}:{tenant.lower()}"


def idempotency_key(canonical_key: str, route: ApplyRoute, destination_identity: str, bundle_sha256: str) -> str:
    parts = (canonical_key, route.value, destination_identity, bundle_sha256)
    if not all(parts):
        raise ValueError("every idempotency component is required")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
