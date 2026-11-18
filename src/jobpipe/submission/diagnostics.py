from dataclasses import dataclass
from typing import Protocol

DKIM_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "s1", "mail", "dkim")


class Resolver(Protocol):
    def txt(self, name: str) -> list[str]: ...


class DnsResolver:
    def txt(self, name: str) -> list[str]:
        import dns.exception
        import dns.resolver

        try:
            answers = dns.resolver.resolve(name, "TXT", lifetime=5)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return []
        except dns.exception.DNSException as exc:
            raise RuntimeError(f"DNS lookup for {name} failed: {exc}") from exc
        return ["".join(part.decode() for part in rdata.strings) for rdata in answers]


@dataclass(frozen=True)
class MailFinding:
    check: str
    ok: bool
    detail: str


def check_sender_domain(sender: str, resolver: Resolver) -> list[MailFinding]:
    domain = sender.partition("@")[2].lower()
    if not domain:
        return [MailFinding("sender", False, "the sender address has no domain")]
    findings: list[MailFinding] = []
    spf = [r for r in resolver.txt(domain) if r.lower().startswith("v=spf1")]
    findings.append(MailFinding("spf", bool(spf), spf[0] if spf else f"no SPF record on {domain}"))
    if spf:
        strict = "-all" in spf[0] or "~all" in spf[0]
        findings.append(MailFinding("spf_policy", strict, "ends with -all or ~all" if strict else "the record has no restrictive qualifier"))
    dmarc = [r for r in resolver.txt(f"_dmarc.{domain}") if r.lower().startswith("v=dmarc1")]
    findings.append(MailFinding("dmarc", bool(dmarc), dmarc[0] if dmarc else f"no DMARC record on _dmarc.{domain}"))
    found = [s for s in DKIM_SELECTORS if any(r.lower().startswith("v=dkim1") or "p=" in r.lower() for r in resolver.txt(f"{s}._domainkey.{domain}"))]
    findings.append(MailFinding("dkim", bool(found), f"selectors found: {', '.join(found)}" if found else
                                "no DKIM key under the common selectors; check your provider for its selector"))
    return findings
