from dataclasses import dataclass, field

from jobpipe.documents.profile import EducationEntry, ExperienceEntry, Profile, ProjectEntry
from jobpipe.documents.selection import SelectionPlan

HEADINGS = {
    "fr": {"experience": "Expérience professionnelle", "projects": "Projets", "education": "Formation",
           "skills": "Compétences", "languages": "Langues", "present": "présent", "client": "Projet client",
           "understand": "compréhension", "speak": "expression orale", "write": "écrit"},
    "en": {"experience": "Work experience", "projects": "Projects", "education": "Education",
           "skills": "Skills", "languages": "Languages", "present": "present", "client": "Client project",
           "understand": "comprehension", "speak": "speaking", "write": "writing"},
}


@dataclass(frozen=True)
class Run:
    """A stretch of a paragraph with its own weight: the role in bold, the organisation in regular."""

    text: str
    bold: bool = False
    muted: bool = False


@dataclass(frozen=True)
class Block:
    """One paragraph. ``text`` is the plain text the guards, the .txt copy and the evaluation read.

    ``runs`` (optional) split the paragraph into weights, and ``aside`` is text set flush right on
    the same line (the dates of an entry). The files hold ``paragraph_text``: the runs, a tab and
    the aside; a heading is written in capitals; a bullet is a real list item, so it has no prefix.
    """

    style: str
    text: str
    source_id: str | None = None
    runs: tuple[Run, ...] = ()
    aside: str | None = None

    @property
    def paragraph_text(self) -> str:
        body = "".join(run.text for run in self.runs) if self.runs else self.text
        if self.style == "heading":
            body = body.upper()
        return f"{body}\t{self.aside}" if self.aside else body

    def segments(self) -> tuple[Run, ...]:
        """The runs to draw, in order: the paragraph text, not the aside."""
        if self.runs:
            return self.runs
        return (Run(self.paragraph_text if self.style == "heading" else self.text),)


@dataclass
class RenderDoc:
    kind: str
    language: str
    blocks: list[Block] = field(default_factory=list)

    def add(self, style: str, text: str, source_id: str | None = None, runs: tuple[Run, ...] = (),
            aside: str | None = None) -> None:
        self.blocks.append(Block(style, text, source_id, runs, aside))

    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    def source_ids(self) -> list[str]:
        return [b.source_id for b in self.blocks if b.source_id]

    def headings(self) -> list[str]:
        return [b.text for b in self.blocks if b.style == "heading"]


def month_label(value: str | None, language: str) -> str:
    if value is None:
        return HEADINGS[language]["present"]
    return f"{value[5:]}/{value[:4]}"


def _language_line(profile: Profile, language: str) -> list[tuple[str, str, str]]:
    labels = HEADINGS[language]
    lines = []
    for skill in profile.languages:
        name = skill.name.get(language) or skill.name.get("en") or skill.code
        levels = {"understand": skill.cefr.comprehension, "speak": skill.cefr.speaking, "write": skill.cefr.writing}
        if len(set(levels.values())) == 1:
            detail = skill.cefr.comprehension + (" (natif)" if skill.is_native and language == "fr" else " (native)" if skill.is_native else "")
        else:
            grouped: dict[str, list[str]] = {}
            for modality, level in levels.items():
                grouped.setdefault(level, []).append(labels[modality])
            detail = " / ".join(f"{level} ({', '.join(modalities)})" for level, modalities in grouped.items())
        lines.append((name, f" — {detail}", f"lang.{skill.code}"))
    return lines


def _experience_header(entry: ExperienceEntry, language: str) -> tuple[str, tuple[Run, ...], str]:
    """The flat text, the runs and the dates of an experience entry."""
    role = entry.role_fr if language == "fr" else entry.role_en
    period = f"{month_label(entry.start, language)} – {month_label(entry.end, language)}"
    if entry.organisation_kind == "multiple_employers":
        return f"{role} ({period})", (Run(role, bold=True),), period
    return f"{role} — {entry.organisation} ({period})", (Run(role, bold=True), Run(f" — {entry.organisation}")), period


def _project_header(entry: ProjectEntry, language: str) -> tuple[str, tuple[Run, ...]]:
    role = entry.role_fr if language == "fr" else entry.role_en
    header = entry.name + (f" — {role}" if role else "")
    runs = [Run(entry.name, bold=True)]
    if role:
        runs.append(Run(f" — {role}"))
    if entry.client:
        label = HEADINGS[language]["client"]
        note = f"{label} : {entry.client}" if language == "fr" else f"{label}: {entry.client}"
        header += f" ({note})"
        runs.append(Run(f" ({note})", muted=True))
    return header, tuple(runs)


# Skills are grouped by the category of the profile; a category the profile invents goes under "other".
SKILL_GROUPS = {
    "fr": {"language": "Programmation", "data": "Données", "ml": "Apprentissage automatique", "genai": "IA générative",
           "tooling": "Outils", "other": "Autres"},
    "en": {"language": "Programming", "data": "Data", "ml": "Machine learning", "genai": "Generative AI",
           "tooling": "Tools", "other": "Other"},
}


def _skill_groups(skills: list, language: str) -> list[tuple[str, list]]:
    """Skills in relevance order, gathered by category (the category of the best skill comes first)."""
    labels = SKILL_GROUPS[language]
    groups: dict[str, list] = {}
    for skill in skills:
        key = skill.category if skill.category in labels and skill.category != "other" else "other"
        groups.setdefault(key, []).append(skill)
    return [(labels[key], members) for key, members in groups.items()]


def build_cv_doc(profile: Profile, plan: SelectionPlan, language: str) -> RenderDoc:
    labels = HEADINGS[language]
    identity = profile.identity
    doc = RenderDoc("cv", language)
    title = next(t for t in profile.cv_titles if t.title_id == plan.title_id)
    doc.add("name", f"{identity.given_name} {identity.family_name}", "identity")
    doc.add("title", title.text(language) or title.text("en") or "", plan.title_id)
    contact = [f"{identity.city}, {identity.country_code}", identity.email, identity.phone_e164]
    doc.add("contact", " | ".join(contact), "identity.contact")
    links = [link.url for link in identity.links if "cv" in link.show_in]
    if links:
        doc.add("contact", " | ".join(links), "identity.links")
    chosen = set(plan.bullet_ids)
    experience = [e for e in profile.experience if e.entry_id in plan.experience_ids]
    if experience:
        doc.add("heading", labels["experience"])
        for entry in experience:
            text, runs, period = _experience_header(entry, language)
            doc.add("entry", text, entry.entry_id, runs, period)
            context = entry.context_phrasings.get(language)
            if context:
                doc.add("line", context, f"{entry.entry_id}.context")
            for bullet in entry.bullets:
                if bullet.bullet_id in chosen and bullet.text(language):
                    doc.add("bullet", bullet.text(language), bullet.bullet_id)
    projects = [next(p for p in profile.projects if p.entry_id == pid) for pid in plan.project_ids]
    if projects:
        doc.add("heading", labels["projects"])
        for entry in projects:
            text, runs = _project_header(entry, language)
            doc.add("entry", text, entry.entry_id, runs)
            for bullet in entry.bullets:
                if bullet.bullet_id in chosen and bullet.text(language):
                    doc.add("bullet", bullet.text(language), bullet.bullet_id)
    education: list[EducationEntry] = [e for e in profile.education if e.entry_id in plan.education_ids]
    if education:
        doc.add("heading", labels["education"])
        for entry in education:
            doc.add("bullet", entry.phrasings.get(language) or "", entry.entry_id)
    skills = [next(s for s in profile.skills if s.skill_id == sid) for sid in plan.skill_ids]
    if skills:
        doc.add("heading", labels["skills"])
        for group, members in _skill_groups(skills, language):
            items = ", ".join(s.label for s in members)
            label = f"{group}\u00a0: " if language == "fr" else f"{group}: "  # French sets a no-break space before a colon
            doc.add("line", label + items, "skills", (Run(label, bold=True), Run(items)))
    doc.add("heading", labels["languages"])
    for name, detail, source in _language_line(profile, language):
        doc.add("bullet", name + detail, source, (Run(name, bold=True), Run(detail)))
    return doc
