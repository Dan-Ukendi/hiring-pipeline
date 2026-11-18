from dataclasses import dataclass

from jobpipe.contract.enums import HoldReason, LanguageSource, RequirementLevel
from jobpipe.contract.models import OfferLanguageRequirement
from jobpipe.documents.profile import Profile

CONFIDENCE_FLOOR = 0.8
AUTHORABLE = ("fr", "en")


@dataclass(frozen=True)
class LanguageDecision:
    language: str
    source: LanguageSource
    hold_reason: HoldReason | None = None
    offer_language: str | None = None


def route_language(
    offer_language: str | None,
    offer_method: str | None,
    offer_confidence: float | None,
    requirements: list[OfferLanguageRequirement],
    profile: Profile,
    allow_nl_generation: bool = False,
) -> LanguageDecision:
    hold = None
    if any(r.language == "nl" and r.level is RequirementLevel.REQUIRED for r in requirements):
        hold = HoldReason.NL_REQUIRED
    if offer_language is None:
        return LanguageDecision("en", LanguageSource.DOWNGRADE, hold, None)
    trusted = offer_method == "declared" or (offer_confidence is not None and offer_confidence >= CONFIDENCE_FLOOR)
    if not trusted:
        return LanguageDecision("en", LanguageSource.DOWNGRADE, hold, offer_language)
    if offer_language == "nl" and allow_nl_generation and profile.can_author("nl"):
        return LanguageDecision("nl", LanguageSource.DETECTED, hold, offer_language)
    if offer_language in AUTHORABLE and profile.can_author(offer_language):
        source = LanguageSource.DECLARED if offer_method == "declared" else LanguageSource.DETECTED
        return LanguageDecision(offer_language, source, hold, offer_language)
    return LanguageDecision("en", LanguageSource.DOWNGRADE, hold, offer_language)
