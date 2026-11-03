from dataclasses import dataclass

from jobpipe.apply.forms import ParsedField
from jobpipe.apply.rules import ApplyRules
from jobpipe.apply.text import contains_phrase, normalise_label
from jobpipe.contract.enums import (
    DocumentSlotKind,
    FieldClass,
    FieldType,
    ProfileKeySource,
    SemanticTag,
)

_COVER = ("cover letter", "lettre de motivation", "motivation", "motivatiebrief", "lettre")
_RESUME = ("resume", "cv", "curriculum", "curriculum vitae")
_PORTFOLIO = ("portfolio", "work sample", "samples")
_TRANSCRIPT = ("transcript", "diploma", "releve de notes", "certificate", "diplome")


@dataclass(frozen=True)
class Resolution:
    field_class: FieldClass
    field_type: FieldType
    profile_key: str | None
    profile_key_source: ProfileKeySource
    semantic_tag: SemanticTag | None
    needs_human: bool
    slot: DocumentSlotKind | None


class FieldResolver:
    def __init__(self, rules: ApplyRules) -> None:
        self._rules = rules
        self._tags = [(SemanticTag(tag), phrases) for tag, phrases in rules.semantics.tags.items()]
        self._labels = [(key, phrases) for key, phrases in rules.synonyms.labels.items()]
        self._exact = rules.synonyms.exact
        self._profile = rules.profile
        self._consent_labels = rules.semantics.consent_markers.labels

    def semantic_tag(self, label: str | None, field_key: str) -> SemanticTag | None:
        text = normalise_label(f"{label or ''} {field_key}")
        for tag, phrases in self._tags:
            if any(contains_phrase(text, p) for p in phrases):
                return tag
        return None

    def field_type(self, parsed: ParsedField, tag: SemanticTag | None) -> FieldType:
        html = parsed.html_type
        if html == "file":
            return FieldType.FILE
        if parsed.tag == "textarea":
            return FieldType.TEXTAREA
        if parsed.tag == "select":
            return FieldType.MULTISELECT if parsed.allows_multiple else FieldType.SELECT
        if html == "radio":
            return FieldType.SELECT
        if html == "checkbox":
            return FieldType.CONSENT if self._is_consent(parsed, tag) else FieldType.BOOLEAN
        mapping = {"email": FieldType.EMAIL, "tel": FieldType.PHONE, "url": FieldType.URL, "number": FieldType.NUMBER,
                   "date": FieldType.DATE}
        kind = {"phone": "tel"}.get(html, html)
        return mapping.get(kind, FieldType.TEXT)

    def _is_consent(self, parsed: ParsedField, tag: SemanticTag | None) -> bool:
        text = normalise_label(parsed.label)
        return tag is SemanticTag.GDPR_CONSENT or any(contains_phrase(text, m) for m in self._consent_labels)

    def _profile_key(self, parsed: ParsedField) -> tuple[str | None, ProfileKeySource]:
        key = normalise_label(parsed.field_key).replace(" ", "_")
        mapped = self._exact.get(key) or self._exact.get(parsed.field_key.lower())
        if mapped and mapped in self._profile.all_keys():
            return mapped, ProfileKeySource.EXACT
        label = normalise_label(parsed.label)
        if label:
            for profile_key, phrases in self._labels:
                if any(label == normalise_label(p) or contains_phrase(label, p) for p in phrases):
                    return profile_key, ProfileKeySource.SYNONYM
        return None, ProfileKeySource.UNRESOLVED

    def slot_for(self, parsed: ParsedField) -> DocumentSlotKind:
        text = normalise_label(f"{parsed.label or ''} {parsed.field_key}")
        for kind, phrases in (
            (DocumentSlotKind.COVER_LETTER, _COVER),
            (DocumentSlotKind.RESUME, _RESUME),
            (DocumentSlotKind.PORTFOLIO, _PORTFOLIO),
            (DocumentSlotKind.TRANSCRIPT, _TRANSCRIPT),
        ):
            if any(contains_phrase(text, p) for p in phrases):
                return kind
        return DocumentSlotKind.OTHER

    def resolve(self, parsed: ParsedField, sole_required_file: bool) -> Resolution:
        tag = self.semantic_tag(parsed.label, parsed.field_key)
        field_type = self.field_type(parsed, tag)
        if field_type is FieldType.FILE:
            slot = self.slot_for(parsed)
            source = ProfileKeySource.EXACT
            profile_key: str | None = slot.value if slot in {DocumentSlotKind.RESUME, DocumentSlotKind.COVER_LETTER} else None
            if slot is DocumentSlotKind.OTHER and sole_required_file and parsed.is_required:
                slot, profile_key, source = DocumentSlotKind.RESUME, "resume", ProfileKeySource.TYPE_DEFAULT
            elif profile_key is None:
                source = ProfileKeySource.UNRESOLVED
            return Resolution(FieldClass.DOCUMENT, field_type, profile_key, source, tag,
                              parsed.is_required and profile_key is None, slot)
        if field_type is FieldType.CONSENT:
            return Resolution(FieldClass.CONSENT, field_type, None, ProfileKeySource.UNRESOLVED,
                              tag or SemanticTag.GDPR_CONSENT, False, None)
        profile_key, source = self._profile_key(parsed)
        if tag in {SemanticTag.EEO, SemanticTag.DIVERSITY} and profile_key is None:
            return Resolution(FieldClass.COMPLIANCE, field_type, None, ProfileKeySource.UNRESOLVED, tag,
                              parsed.is_required and not parsed.is_hidden, None)
        if profile_key in self._profile.contact_keys:
            field_class = FieldClass.STANDARD
        else:
            field_class = FieldClass.CUSTOM_QUESTION
        needs_human = parsed.is_required and not parsed.is_hidden and profile_key is None
        return Resolution(field_class, field_type, profile_key, source, tag, needs_human, None)
