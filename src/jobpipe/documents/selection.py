import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, ConfigDict

from jobpipe.contract.enums import RequirementLevel
from jobpipe.documents.profile import Bullet, ExperienceEntry, Profile, ProjectEntry, months_between

LEVEL_WEIGHT = {RequirementLevel.REQUIRED: 1.0, RequirementLevel.PREFERRED: 0.5, RequirementLevel.MENTIONED: 0.25}
ENGAGEMENT_WEIGHT = {
    "client_brief_delivered_to_production": 1.0,
    "founder": 0.9,
    "employment": 0.9,
    "team_proof_of_concept": 0.7,
    "personal": 0.5,
}
MAX_SKILLS = 12
MAX_PROJECTS = 3


class SelectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title_id: str
    experience_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    education_ids: tuple[str, ...]
    bullet_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    claim_ids: tuple[str, ...] = ()
    gap_skills: tuple[str, ...] = ()
    llm_reordered: bool = False

    def identifiers(self) -> list[str]:
        return [self.title_id, *self.experience_ids, *self.project_ids, *self.education_ids, *self.bullet_ids,
                *self.skill_ids, *self.claim_ids]


@dataclass
class Scores:
    entries: dict[str, float] = field(default_factory=dict)
    bullets: dict[str, float] = field(default_factory=dict)
    skills: dict[str, float] = field(default_factory=dict)
    titles: dict[str, float] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    matched: set[str] = field(default_factory=set)


def normalise_skill(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _profile_skill_keys(profile: Profile) -> dict[str, str]:
    keys: dict[str, str] = {}
    for skill in profile.skills:
        keys[normalise_skill(skill.skill_id.removeprefix("skill."))] = skill.skill_id
        keys[normalise_skill(skill.label)] = skill.skill_id
        for alias in skill.aliases:
            keys[normalise_skill(alias)] = skill.skill_id
    return keys


def _recency(owner_end: str | None, today: date) -> float:
    if owner_end is None:
        return 1.0
    months = months_between(owner_end, f"{today.year}-{today.month:02d}", today)
    return max(0.4, 1.0 - months / 120.0)


def prescore(profile: Profile, requirements: list[tuple[str, RequirementLevel]], today: date) -> Scores:
    weights: dict[str, float] = {}
    for skill, level in requirements:
        key = normalise_skill(skill)
        weights[key] = max(weights.get(key, 0.0), LEVEL_WEIGHT[level])
    skill_keys = _profile_skill_keys(profile)
    scores = Scores()
    for key, weight in weights.items():
        if key in skill_keys:
            scores.matched.add(key)
        elif weight >= LEVEL_WEIGHT[RequirementLevel.PREFERRED]:
            scores.gaps.append(key)
    scores.gaps.sort()

    def tag_overlap(tags: tuple[str, ...]) -> float:
        return sum(weights.get(normalise_skill(tag), 0.0) for tag in set(tags))

    owners: list[ExperienceEntry | ProjectEntry] = [*profile.experience, *profile.projects]
    for owner in owners:
        tags = tuple(getattr(owner, "tags", ())) + tuple(getattr(owner, "stack", ()))
        end = owner.end if hasattr(owner, "end") else None
        weight = ENGAGEMENT_WEIGHT.get(owner.engagement_kind, 0.4) * _recency(end, today)
        entry_overlap = tag_overlap(tags)
        scores.entries[owner.entry_id] = round(entry_overlap * weight + weight * 0.1, 4)
        for bullet in owner.bullets:
            scores.bullets[bullet.bullet_id] = round((tag_overlap(bullet.tags) + 0.3 * entry_overlap) * weight + (0.5 if not bullet.droppable else 0.0), 4)
    for skill in profile.skills:
        match = max(
            (weights.get(normalise_skill(name), 0.0) for name in (skill.skill_id.removeprefix("skill."), skill.label, *skill.aliases)),
            default=0.0,
        )
        evidence = sum(scores.entries.get(ref, 0.0) for ref in skill.evidence_refs)
        scores.skills[skill.skill_id] = round(match * 10 + evidence, 4)
    for title in profile.cv_titles:
        scores.titles[title.title_id] = tag_overlap(title.tags)
    return scores


def _ordered(items: list[str], scores: dict[str, float]) -> list[str]:
    position = {item: index for index, item in enumerate(items)}
    return sorted(items, key=lambda item: (-scores.get(item, 0.0), position[item]))


def deterministic_plan(profile: Profile, scores: Scores, language: str) -> SelectionPlan:
    titles = [t.title_id for t in profile.cv_titles if t.text(language)] or [t.title_id for t in profile.cv_titles]
    title_id = _ordered(titles, scores.titles)[0]
    projects = _ordered([p.entry_id for p in profile.projects], scores.entries)[:MAX_PROJECTS]
    experience = [e.entry_id for e in profile.experience]
    bullets: list[str] = []
    for owner in (*[e for e in profile.experience], *[p for p in profile.projects if p.entry_id in projects]):
        usable = [b for b in owner.bullets if b.text(language)]
        bullets.extend(b.bullet_id for b in _bullet_order(usable, scores.bullets))
    skills = [s for s in _ordered([s.skill_id for s in profile.skills], scores.skills)][:MAX_SKILLS]
    education = [e.entry_id for e in profile.education if e.disclosure_policy != "omit" and e.phrasings.get(language)]
    return SelectionPlan(
        title_id=title_id, experience_ids=tuple(experience), project_ids=tuple(projects), education_ids=tuple(education),
        bullet_ids=tuple(bullets), skill_ids=tuple(skills), gap_skills=tuple(scores.gaps),
    )


def _bullet_order(bullets: list[Bullet], scores: dict[str, float]) -> list[Bullet]:
    position = {b.bullet_id: i for i, b in enumerate(bullets)}
    return sorted(bullets, key=lambda b: (-scores.get(b.bullet_id, 0.0), position[b.bullet_id]))


def drop_lowest_bullet(profile: Profile, plan: SelectionPlan, scores: Scores) -> SelectionPlan | None:
    droppable = [
        b.bullet_id for owner in (*profile.experience, *profile.projects) for b in owner.bullets
        if b.droppable and b.bullet_id in plan.bullet_ids
    ]
    if not droppable:
        return None
    victim = min(droppable, key=lambda bid: (scores.bullets.get(bid, 0.0), bid))
    return plan.model_copy(update={"bullet_ids": tuple(b for b in plan.bullet_ids if b != victim)})
