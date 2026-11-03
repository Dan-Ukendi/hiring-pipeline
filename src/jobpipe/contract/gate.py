from jobpipe.contract.enums import LanguageSource, PackageStatus
from jobpipe.contract.models import PackageManifest


def documents_ready(manifest: PackageManifest, l4_unsupported_limit: int) -> bool:
    return (
        manifest.status is PackageStatus.READY
        and manifest.guard_summary.blocking == 0
        and manifest.guard_summary.l4_unsupported <= l4_unsupported_limit
    )


def needs_manual(manifest: PackageManifest) -> bool:
    return manifest.status in {PackageStatus.HELD, PackageStatus.SUPERSEDED, PackageStatus.DRAFT}


def language_mismatch(offer_language: str | None, document_language: str, source: LanguageSource) -> bool:
    if offer_language is None or document_language == offer_language:
        return False
    if document_language == "en" and source is LanguageSource.DOWNGRADE:
        return False
    return True
