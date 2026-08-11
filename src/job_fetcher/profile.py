from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "data" / "profile.json"
EXAMPLE_PROFILE_PATH = ROOT / "config" / "profile.example.json"


class ProfileError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"Profile file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Invalid profile JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("Profile root must be a JSON object")
    return data


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    candidate = profile.get("candidate") or {}
    scoring = profile.get("scoring") or {}
    if not isinstance(candidate.get("experienceYears"), (int, float)):
        errors.append("candidate.experienceYears must be numeric")
    if not profile.get("roleFamilies"):
        errors.append("roleFamilies must contain at least one family")
    if not profile.get("primarySkills"):
        errors.append("primarySkills must contain at least one skill")
    for key in ("highPriorityScore", "goodCandidateScore", "relevantMinScore", "lowPriorityMinScore"):
        if not isinstance(scoring.get(key), (int, float)):
            errors.append(f"scoring.{key} must be numeric")
    if all(isinstance(scoring.get(k), (int, float)) for k in (
        "highPriorityScore", "goodCandidateScore", "relevantMinScore", "lowPriorityMinScore"
    )):
        high = float(scoring["highPriorityScore"])
        good = float(scoring["goodCandidateScore"])
        relevant = float(scoring["relevantMinScore"])
        low = float(scoring["lowPriorityMinScore"])
        if not (0 <= low <= relevant <= good <= high <= 100):
            errors.append("score thresholds must satisfy 0 <= low <= relevant <= good <= high <= 100")
    return errors


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else PROFILE_PATH
    if not target.exists() and EXAMPLE_PROFILE_PATH.exists():
        target = EXAMPLE_PROFILE_PATH
    profile = _read_json(target)
    # Backward compatibility with the pre-GitHub Step 10 profile key.
    scoring = profile.setdefault("scoring", {})
    if "relevantMinScore" not in scoring and "aiCandidateMinScore" in scoring:
        scoring["relevantMinScore"] = scoring["aiCandidateMinScore"]
    errors = validate_profile(profile)
    if errors:
        raise ProfileError("Invalid candidate profile:\n" + "\n".join(f"- {e}" for e in errors))
    return deepcopy(profile)


def save_profile(profile: dict[str, Any], path: str | Path | None = None) -> Path:
    errors = validate_profile(profile)
    if errors:
        raise ProfileError("Invalid candidate profile:\n" + "\n".join(f"- {e}" for e in errors))
    target = Path(path) if path else PROFILE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
