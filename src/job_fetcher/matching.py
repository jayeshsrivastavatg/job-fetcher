from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from typing import Any, Iterable


SPACE_RE = re.compile(r"\s+")
NONWORD_RE = re.compile(r"[^a-z0-9+#./ -]+")


@dataclass
class ExperienceRequirement:
    min_years: float | None = None
    max_years: float | None = None
    text: str | None = None
    source: str = "unknown"


@dataclass
class ScoreResult:
    role_family: str
    role_label: str
    normalized_location: str
    min_experience: float | None
    max_experience: float | None
    experience_text: str | None
    role_score: float
    experience_score: float
    primary_skill_score: float
    supporting_score: float
    relevance_score: float
    relevance_status: str
    is_relevant: bool
    hard_filtered: bool
    filter_reason: str | None
    matched_primary: list[dict[str, Any]]
    matched_supporting: list[dict[str, Any]]
    matched_role_signals: list[str]
    required_text_found: bool
    preferred_text_found: bool
    score_breakdown: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: str | None) -> str:
    text = (value or "").lower().replace("–", "-").replace("—", "-")
    text = NONWORD_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def job_source_hash(row: dict[str, Any] | Any) -> str:
    def get(key: str):
        try:
            return row[key]
        except Exception:
            return getattr(row, key, None)
    payload = "\n".join([
        str(get("title") or "").strip(),
        str(get("location") or "").strip(),
        str(get("description") or "").strip(),
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def normalize_location(location: str | None) -> str:
    raw = normalize_text(location)
    if not raw:
        return "Unknown"
    if "remote" in raw and "india" in raw:
        return "Remote, India"
    aliases = [
        (("bengaluru", "bangalore"), "Bengaluru, India"),
        (("gurugram", "gurgaon"), "Gurugram, India"),
        (("noida",), "Noida, India"),
        (("hyderabad",), "Hyderabad, India"),
        (("pune",), "Pune, India"),
        (("chennai",), "Chennai, India"),
        (("mumbai",), "Mumbai, India"),
        (("delhi", "new delhi"), "Delhi, India"),
        (("india",), "India"),
    ]
    for needles, canonical in aliases:
        if any(n in raw for n in needles):
            return canonical
    if "remote" in raw:
        return "Remote"
    return (location or "Unknown").strip()


def is_explicit_foreign_location(location: str | None) -> bool:
    text = normalize_text(location)
    if not text or "india" in text:
        return False
    if "remote" in text and not any(x in text for x in ("united states", " usa ", "canada", "united kingdom", "singapore", "australia", "germany", "france", "ireland", "poland", "romania")):
        return False
    foreign = (
        "united states", "usa", "canada", "united kingdom", "uk", "singapore", "australia",
        "germany", "france", "ireland", "poland", "romania", "netherlands", "spain", "sweden",
        "switzerland", "japan", "korea", "brazil", "mexico",
    )
    return any(token in text for token in foreign)


def _section_text(description: str | None, headings: tuple[str, ...]) -> str:
    """Best-effort extraction of a section until the next likely heading.

    JDs are messy, so failure to identify a section should never reject a job.
    """
    if not description:
        return ""
    lines = [line.strip() for line in description.splitlines()]
    start = None
    out: list[str] = []
    generic_heading = re.compile(r"^[A-Za-z][A-Za-z /&+-]{2,55}:?$", re.I)
    for i, line in enumerate(lines):
        low = normalize_text(line)
        if start is None and any(h in low for h in headings):
            start = i + 1
            continue
        if start is not None and i >= start:
            if out and generic_heading.match(line) and len(line.split()) <= 7:
                low_heading = normalize_text(line)
                if not any(h in low_heading for h in headings):
                    break
            out.append(line)
    return "\n".join(out).strip()


def split_requirement_sections(description: str | None) -> tuple[str, str, str]:
    required = _section_text(description, (
        "required qualifications", "minimum qualifications", "required skills", "requirements",
        "what you need", "what youll need", "must have", "basic qualifications",
    ))
    preferred = _section_text(description, (
        "preferred qualifications", "preferred skills", "nice to have", "good to have",
        "bonus", "desired qualifications",
    ))
    return required, preferred, description or ""


def _experience_matches(text: str) -> list[ExperienceRequirement]:
    clean = text.replace("–", "-").replace("—", "-")
    results: list[ExperienceRequirement] = []
    range_re = re.compile(
        r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:-|to)\s*(\d{1,2}(?:\.\d+)?)\s*(?:\+\s*)?(?:years?|yrs?)\b",
        re.I,
    )
    plus_re = re.compile(
        r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:\+|plus|or more|and above|minimum|min\.?)?\s*(?:years?|yrs?)\b",
        re.I,
    )
    occupied: list[tuple[int, int]] = []
    for m in range_re.finditer(clean):
        lo, hi = float(m.group(1)), float(m.group(2))
        if hi < lo:
            lo, hi = hi, lo
        results.append(ExperienceRequirement(lo, hi, m.group(0)))
        occupied.append(m.span())
    for m in plus_re.finditer(clean):
        if any(m.start() >= a and m.end() <= b for a, b in occupied):
            continue
        val = float(m.group(1))
        context = clean[max(0, m.start()-25):m.end()+10].lower()
        max_years = None
        # Plain "3 years" in a qualification section is usually a minimum;
        # retain it as a minimum instead of inventing an upper bound.
        results.append(ExperienceRequirement(val, max_years, m.group(0)))
    return [r for r in results if 0 <= (r.min_years or 0) <= 25 and (r.max_years is None or r.max_years <= 30)]


def parse_experience(description: str | None) -> ExperienceRequirement:
    required, preferred, all_text = split_requirement_sections(description)
    for label, text in (("required", required), ("all", all_text), ("preferred", preferred)):
        matches = _experience_matches(text)
        if matches:
            # Avoid a stray higher number in benefits/leadership prose by using the
            # smallest plausible required minimum. If two ranges share the same
            # minimum, prefer the bounded one because it is more informative.
            matches.sort(key=lambda r: (r.min_years if r.min_years is not None else 99, r.max_years is None))
            best = matches[0]
            best.source = label
            return best
    return ExperienceRequirement()


def experience_score(requirement: ExperienceRequirement, profile: dict[str, Any]) -> tuple[float, str | None, bool]:
    years = float((profile.get("candidate") or {}).get("experienceYears", 5.0))
    policy = profile.get("experiencePolicy") or {}
    hard_reject_min = float(policy.get("hardRejectMinimumYears", 8.0))
    lo, hi = requirement.min_years, requirement.max_years
    if lo is None:
        return 18.0, None, False
    if lo >= hard_reject_min:
        return 0.0, f"requires_{lo:g}+_years", True
    if hi is not None and lo <= years <= hi:
        return 20.0, None, False
    if hi is None and 2 <= lo <= years:
        return 20.0, None, False
    if hi is not None and hi < years:
        if hi >= years - 1:
            return 14.0, "slightly_under_level", False
        if hi >= 3:
            return 8.0, "under_level", False
        return 5.0, "significantly_under_level", False
    if lo > years:
        gap = lo - years
        if gap <= 1:
            return 12.0, "one_year_stretch", False
        if gap <= 2:
            return 5.0, "two_year_stretch", False
        return 0.0, f"requires_{lo:g}+_years", lo >= hard_reject_min
    if lo < 2:
        return 12.0, "junior_leaning", False
    return 15.0, None, False


def _contains_alias(text: str, alias: str) -> bool:
    alias = normalize_text(alias)
    if not alias:
        return False
    # Word boundaries are useful for short tokens like java/sql but punctuation
    # in node.js / ci/cd makes a plain normalized substring more reliable.
    if len(alias) <= 4 and alias.replace("+", "").isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def _skill_context_multiplier(aliases: Iterable[str], required: str, preferred: str, full: str) -> tuple[float, str | None]:
    req = normalize_text(required)
    pref = normalize_text(preferred)
    all_text = normalize_text(full)
    for alias in aliases:
        if _contains_alias(req, alias):
            return 1.0, "required"
    for alias in aliases:
        if _contains_alias(pref, alias):
            return 0.55, "preferred"
    for alias in aliases:
        if _contains_alias(all_text, alias):
            return 0.85, "general"
    return 0.0, None


def score_skills(skills: list[dict[str, Any]], required: str, preferred: str, full: str, cap: float) -> tuple[float, list[dict[str, Any]]]:
    total = 0.0
    matched: list[dict[str, Any]] = []
    for skill in skills or []:
        aliases = skill.get("aliases") or [skill.get("name", "")]
        mult, context = _skill_context_multiplier(aliases, required, preferred, full)
        if not mult:
            continue
        base = float(skill.get("weight", 0))
        points = base * mult
        total += points
        matched.append({
            "name": skill.get("name"), "points": round(points, 2), "max_points": base,
            "context": context, "evidence": skill.get("evidence", "unknown"),
        })
    return round(min(cap, total), 2), matched


def _signal_presence(text: str) -> dict[str, bool]:
    t = normalize_text(text)
    has = lambda *terms: any(_contains_alias(t, term) for term in terms)
    return {
        "java": has("java"),
        "spring": has("spring boot", "spring framework", "spring"),
        "backend": has("backend", "back-end", "server side", "server-side"),
        "rest_api": has("rest api", "restful", "api", "web services"),
        "microservices": has("microservices", "microservice"),
        "distributed_systems": has("distributed systems", "distributed system"),
        "sql": has("postgresql", "postgres", "sql", "mysql", "relational database"),
        "react": has("react", "reactjs", "react.js"),
        "node": has("node.js", "nodejs", "node js"),
        "typescript": has("typescript"),
        "javascript": has("javascript", "ecmascript"),
        "fullstack": has("full stack", "full-stack", "fullstack"),
    }


def _role_family_scores(title: str, description: str, profile: dict[str, Any]) -> tuple[str, str, float, list[str], dict[str, float]]:
    text = f"{title}\n{description}"
    s = _signal_presence(text)
    title_n = normalize_text(title)
    title_backend = any(x in title_n for x in ("backend", "back end", "platform", "server"))
    title_fullstack = any(x in title_n for x in ("full stack", "fullstack", "full-stack"))
    title_software = any(x in title_n for x in ("software engineer", "software developer", "sde", "member of technical staff", "mts"))

    values: dict[str, float] = {}
    matched: dict[str, list[str]] = {}

    jb = 0.0; ms: list[str] = []
    for key, pts in (("java",10),("spring",10),("backend",8),("rest_api",4),("microservices",3),("distributed_systems",3),("sql",2)):
        present = s[key] or (key == "backend" and title_backend)
        if present:
            jb += pts; ms.append(key)
    if s["java"] and title_software and not title_backend:
        jb += 2
    values["java_backend"] = min(40.0, jb); matched["java_backend"] = ms

    jf = 0.0; ms = []
    if s["java"]:
        jf += 10; ms.append("java")
    if s["spring"]:
        jf += 10; ms.append("spring")
    if s["react"]:
        jf += 8; ms.append("react")
    if s["typescript"] or s["javascript"]:
        jf += 6; ms.append("typescript_javascript")
    if title_fullstack or s["fullstack"] or s["rest_api"] or s["backend"]:
        jf += 6; ms.append("fullstack_api")
    values["java_react_fullstack"] = min(40.0, jf); matched["java_react_fullstack"] = ms

    nb = 0.0; ms = []
    for key, pts in (("node",12),("typescript",10),("javascript",5),("react",5),("sql",2)):
        if s[key]: nb += pts; ms.append(key)
    if s["backend"] or title_backend or s["rest_api"]:
        nb += 6; ms.append("backend_api")
    values["node_backend"] = min(40.0, nb); matched["node_backend"] = ms

    nf = 0.0; ms = []
    for key, pts in (("node",10),("typescript",8),("javascript",4),("react",8),("sql",2)):
        if s[key]: nf += pts; ms.append(key)
    if title_fullstack or s["fullstack"] or s["rest_api"] or s["backend"]:
        nf += 8; ms.append("fullstack_api")
    values["node_react_fullstack"] = min(40.0, nf); matched["node_react_fullstack"] = ms

    # Broad SWE roles that list Java or TypeScript among several accepted languages
    # remain viable even when the JD does not literally say backend/full-stack.
    general = 0.0; ms = []
    if title_software:
        general += 10; ms.append("software_engineering_title")
    if s["java"]:
        general += 10; ms.append("java")
    if s["typescript"] or s["javascript"]:
        general += 7; ms.append("typescript_javascript")
    if s["distributed_systems"]:
        general += 5; ms.append("distributed_systems")
    if s["rest_api"]:
        general += 4; ms.append("rest_api")
    if s["sql"]:
        general += 4; ms.append("sql")
    values["software_engineering_general"] = min(36.0, general); matched["software_engineering_general"] = ms

    priorities = {x.get("id"): float(x.get("priority", 0)) for x in profile.get("roleFamilies", [])}
    order = sorted(values, key=lambda k: (values[k], priorities.get(k, 0)), reverse=True)
    family = order[0]
    if values[family] < 5:
        family = "other"
        values["other"] = values.get(order[0], 0.0)
        matched["other"] = matched.get(order[0], [])
    labels = {x.get("id"): x.get("label", x.get("id")) for x in profile.get("roleFamilies", [])}
    labels.setdefault("software_engineering_general", "General Software Engineering")
    labels.setdefault("other", "Other")
    return family, labels.get(family, family), round(values[family], 2), matched[family], values


def _hard_title_filter(title: str, profile: dict[str, Any]) -> str | None:
    t = normalize_text(title)
    for pattern in profile.get("hardExcludeTitlePatterns") or []:
        p = normalize_text(pattern)
        if p and p in t:
            return f"excluded_title:{pattern}"
    return None


def _relevance_status(score: float, hard_filtered: bool, profile: dict[str, Any]) -> tuple[str, bool]:
    if hard_filtered:
        return "filtered", False
    scoring = profile.get("scoring") or {}
    high = float(scoring.get("highPriorityScore", 80))
    good = float(scoring.get("goodCandidateScore", 65))
    relevant_min = float(scoring.get("relevantMinScore", scoring.get("aiCandidateMinScore", 50)))
    low = float(scoring.get("lowPriorityMinScore", 35))
    if score >= high:
        return "high", True
    if score >= good:
        return "good", True
    if score >= relevant_min:
        return "possible", True
    if score >= low:
        return "low", False
    return "filtered", False


def score_job(row: dict[str, Any] | Any, profile: dict[str, Any]) -> ScoreResult:
    def get(key: str, default=None):
        try:
            value = row[key]
        except Exception:
            value = getattr(row, key, default)
        return default if value is None else value

    title = str(get("title", ""))
    description = str(get("description", "") or "")
    location = str(get("location", "") or "")
    normalized_loc = normalize_location(location)
    required, preferred, full = split_requirement_sections(description)

    hard_reason = _hard_title_filter(title, profile)
    location_policy = profile.get("locationPolicy") or {}
    if not hard_reason and location_policy.get("rejectExplicitForeign", True) and is_explicit_foreign_location(location):
        hard_reason = "location_outside_target"

    exp = parse_experience(description)
    exp_score, exp_note, exp_hard = experience_score(exp, profile)
    if exp_hard and not hard_reason:
        hard_reason = exp_note

    family, family_label, role_score, role_signals, family_scores = _role_family_scores(title, description, profile)
    primary_score, matched_primary = score_skills(profile.get("primarySkills") or [], required, preferred, full, 25.0)
    support_score, matched_support = score_skills(profile.get("supportingSkills") or [], required, preferred, full, 15.0)

    total = round(min(100.0, role_score + exp_score + primary_score + support_score), 2)

    # A title can be generic, but if the JD contains almost none of the target
    # stack, do not let experience/supporting skills accidentally promote it.
    if role_score < 10 and primary_score < 5 and not hard_reason:
        hard_reason = "insufficient_target_stack_signal"

    hard_filtered = bool(hard_reason)
    status, is_relevant = _relevance_status(total, hard_filtered, profile)
    if status == "filtered" and not hard_reason:
        hard_reason = "relevance_score_below_threshold"

    return ScoreResult(
        role_family=family,
        role_label=family_label,
        normalized_location=normalized_loc,
        min_experience=exp.min_years,
        max_experience=exp.max_years,
        experience_text=exp.text,
        role_score=round(role_score, 2),
        experience_score=round(exp_score, 2),
        primary_skill_score=round(primary_score, 2),
        supporting_score=round(support_score, 2),
        relevance_score=total,
        relevance_status=status,
        is_relevant=is_relevant,
        hard_filtered=hard_filtered,
        filter_reason=hard_reason,
        matched_primary=matched_primary,
        matched_supporting=matched_support,
        matched_role_signals=role_signals,
        required_text_found=bool(required),
        preferred_text_found=bool(preferred),
        score_breakdown={
            "role": round(role_score, 2), "experience": round(exp_score, 2),
            "primarySkills": round(primary_score, 2), "supporting": round(support_score, 2),
            "experienceNote": exp_note, "roleFamilyScores": {k: round(v, 2) for k, v in family_scores.items()},
        },
    )


def normalize_title_for_dedup(title: str | None) -> str:
    t = normalize_text(title)
    replacements = {
        "software development engineer": "software engineer",
        "sde ii": "software engineer 2",
        "sde 2": "software engineer 2",
        "software engineer ii": "software engineer 2",
        "software engineer - ii": "software engineer 2",
        "sr software engineer": "senior software engineer",
    }
    for src, dst in replacements.items():
        t = t.replace(src, dst)
    return SPACE_RE.sub(" ", t).strip()


def description_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    # Token Jaccard is stable and cheap for long JDs; SequenceMatcher helps for
    # shorter descriptions where token sets are too coarse.
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    if min(len(na), len(nb)) < 1200:
        seq = SequenceMatcher(None, na[:6000], nb[:6000]).ratio()
        return max(jaccard, seq)
    return jaccard


def dedup_group_key(company_id: str, title: str | None, location: str | None) -> str:
    return "|".join([company_id, normalize_title_for_dedup(title), normalize_location(location).lower()])
