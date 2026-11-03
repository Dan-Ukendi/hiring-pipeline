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
class Block:
    style: str
    text: str
    source_id: str | None = None


@dataclass
class RenderDoc:
    kind: str
    language: str
    blocks: list[Block] = field(default_factory=list)

    def add(self, style: str, text: str, source_id: str | None = None) -> None:
        self.blocks.append(Block(style, text, source_id))

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


def _language_line(profile: Profile, language: str) -> list[tuple[str, str]]:
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
        lines.append((f"{name} — {detail}", f"lang.{skill.code}"))
    return lines


def _experience_header(entry: ExperienceEntry, language: str) -> str:
    role = entry.role_fr if language == "fr" else entry.role_en
    period = f"{month_label(entry.start, language)} – {month_label(entry.end, language)}"
    if entry.organisation_kind == "multiple_employers":
        return f"{role} ({period})"
    return f"{role} — {entry.organisation} ({period})"


def _project_header(entry: ProjectEntry, language: str) -> str:
    role = entry.role_fr if language == "fr" else entry.role_en
    header = entry.name + (f" — {role}" if role else "")
    if entry.client:
        header += f" ({HEADINGS[language]['client']} : {entry.client})" if language == "fr" else f" ({HEADINGS[language]['client']}: {entry.client})"
    return header


def build_cv_doc(profile: Profile, plan: SelectionPlan, language: str) -> RenderDoc:
    labels = HEADINGS[language]
    identity = profile.identity
    doc = RenderDoc("cv", language)
    title = next(t for t in profile.cv_titles if t.title_id == plan.title_id)
    doc.add("name", f"{identity.given_name} {identity.family_name}", "identity")
    doc.add("title", title.text(language) or title.text("en") or "", plan.title_id)
    contact = [f"{identity.city}, {identity.country_code}", identity.email, identity.phone_e164]
    contact.extend(link.url for link in identity.links if "cv" in link.show_in)
    doc.add("contact", " | ".join(contact), "identity.contact")
    chosen = set(plan.bullet_ids)
    experience = [e for e in profile.experience if e.entry_id in plan.experience_ids]
    if experience:
        doc.add("heading", labels["experience"])
        for entry in experience:
            doc.add("entry", _experience_header(entry, language), entry.entry_id)
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
            doc.add("entry", _project_header(entry, language), entry.entry_id)
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
        doc.add("line", ", ".join(s.label for s in skills), "skills")
    doc.add("heading", labels["languages"])
    for text, source in _language_line(profile, language):
        doc.add("bullet", text, source)
    return doc
