from enum import StrEnum


class ApplyRoute(StrEnum):
    ATS_API_SUBMIT = "ats_api_submit"
    ATS_HOSTED_FORM = "ats_hosted_form"
    EMPLOYER_SITE_FORM = "employer_site_form"
    EMAIL_APPLY = "email_apply"
    ACCOUNT_PORTAL = "account_portal"
    AGGREGATOR_REDIRECT = "aggregator_redirect"
    CLOSED = "closed"
    UNKNOWN = "unknown"


FORM_ROUTES = frozenset({ApplyRoute.ATS_HOSTED_FORM, ApplyRoute.EMPLOYER_SITE_FORM})


class Feasibility(StrEnum):
    AUTO_FULL = "auto_full"
    AUTO_ASSISTED = "auto_assisted"
    MANUAL_ONLY = "manual_only"
    BLOCKED = "blocked"


class ReasonCode(StrEnum):
    ROUTE_UNDETERMINED = "route_undetermined"
    CAPTCHA_PRESENT = "captcha_present"
    BOT_SHIELD_PRESENT = "bot_shield_present"
    AUTH_REQUIRED = "auth_required"
    UNMAPPED_REQUIRED_FIELDS = "unmapped_required_fields"
    ROBOTS_DISALLOWED = "robots_disallowed"
    POSTING_CLOSED = "posting_closed"
    POLICY_RESTRICTED = "policy_restricted"
    NO_CREDENTIAL = "no_credential"
    PROTECTION_UNCERTAIN = "protection_uncertain"
    AGGREGATOR_UNRESOLVED = "aggregator_unresolved"
    PLAN_PARTIAL = "plan_partial"
    OK = "ok"


class FieldClass(StrEnum):
    STANDARD = "standard"
    CUSTOM_QUESTION = "custom_question"
    COMPLIANCE = "compliance"
    CONSENT = "consent"
    DOCUMENT = "document"


class FieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    SELECT = "select"
    MULTISELECT = "multiselect"
    BOOLEAN = "boolean"
    DATE = "date"
    NUMBER = "number"
    FILE = "file"
    CONSENT = "consent"


class Severity(StrEnum):
    BLOCKING = "blocking"
    DEGRADING = "degrading"
    INFORMATIONAL = "informational"


class ObstacleKind(StrEnum):
    AUTH_REQUIRED = "auth_required"
    CAPTCHA = "captcha"
    BOT_SHIELD = "bot_shield"
    PROTECTION_UNCERTAIN = "protection_uncertain"
    RATE_LIMIT = "rate_limit"
    MULTI_STEP = "multi_step"
    JS_REQUIRED = "js_required"
    UNSTABLE_SELECTOR = "unstable_selector"
    FILE_TYPE_RESTRICTED = "file_type_restricted"
    CREDENTIAL_LEAK_SEEN = "credential_leak_seen"
    TOS_RESTRICTION = "tos_restriction"


class Confidence(StrEnum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class DetectionSource(StrEnum):
    MAILTO = "mailto"
    HOST_FINGERPRINT = "host_fingerprint"
    HTTP_PROBE = "http_probe"
    VENDOR_METADATA = "vendor_metadata"
    STATIC_FORM = "static_form"
    RENDERED_FORM = "rendered_form"
    EMAIL_FALLBACK = "email_fallback"
    ROBOTS = "robots"
    EXHAUSTED = "exhausted"


class DocumentSlotKind(StrEnum):
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    PORTFOLIO = "portfolio"
    TRANSCRIPT = "transcript"
    OTHER = "other"


class UploadMechanism(StrEnum):
    MULTIPART = "multipart"
    BASE64_JSON = "base64_json"
    REMOTE_URL = "remote_url"
    UNKNOWN = "unknown"


class SemanticTag(StrEnum):
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    SALARY_EXPECTATION = "salary_expectation"
    NOTICE_PERIOD = "notice_period"
    LANGUAGE_LEVEL = "language_level"
    YEARS_EXPERIENCE = "years_experience"
    GDPR_CONSENT = "gdpr_consent"
    EEO = "eeo"
    DIVERSITY = "diversity"
    RELOCATION = "relocation"
    DRIVING_LICENCE = "driving_licence"
    SECURITY_CLEARANCE = "security_clearance"
    DEGREE = "degree"


class ProfileKeySource(StrEnum):
    EXACT = "exact"
    SYNONYM = "synonym"
    TYPE_DEFAULT = "type_default"
    UNRESOLVED = "unresolved"


class CredentialKind(StrEnum):
    NONE = "none"
    EMPLOYER_API_KEY = "employer_api_key"
    PARTNER_KEY = "partner_key"
    CAREERS_SITE_TOKEN = "careers_site_token"
    OAUTH = "oauth"


class SubmissionPolicy(StrEnum):
    UNKNOWN = "unknown"
    PERMITTED = "permitted"
    RESTRICTED = "restricted"


class Tier(StrEnum):
    A = "a"
    B = "b"
    C = "c"


class PackageStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    AWAITING_REVIEW = "awaiting_review"
    HELD = "held"
    SUPERSEDED = "superseded"


class ApprovedBy(StrEnum):
    POLICY = "policy"
    HUMAN = "human"


class AnswerSource(StrEnum):
    DECLARATION = "declaration"
    COMPUTED = "computed"
    CLAIMS_BANK = "claims_bank"
    LLM = "llm"
    MANUAL = "manual"


class LanguageSource(StrEnum):
    DETECTED = "detected"
    DECLARED = "declared"
    DOWNGRADE = "downgrade"


class RequirementLevel(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    MENTIONED = "mentioned"


class HoldReason(StrEnum):
    NL_REQUIRED = "nl_required"
    DEGREE_QUESTION = "degree_question"
    EXPERIENCE_THRESHOLD = "experience_threshold"
    GUARD_BLOCKING = "guard_blocking"
    UNSUPPORTED_CLAIMS = "unsupported_claims"
    PLAN_STALE = "plan_stale"
    PLAN_PARTIAL = "plan_partial"
    LAYOUT_OVERFLOW = "layout_overflow"
    LETTER_UNAVAILABLE = "letter_unavailable"
    FORMAT_UNSUPPORTED = "format_unsupported"
    ANSWER_NEEDS_HUMAN = "answer_needs_human"
    PLAN_REJECTED = "plan_rejected"
    RENDER_MISMATCH = "render_mismatch"


class RunPhase(StrEnum):
    DISCOVERY = "discovery"
    APPLY_PLAN = "apply_plan"
    DOCUMENTS = "documents"
    SUBMISSION = "submission"
    CHAIN = "chain"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
