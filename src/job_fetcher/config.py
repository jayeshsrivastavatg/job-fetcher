from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "companies.yaml"
SUPPORTED_SOURCES = {
    "auto", "greenhouse", "lever", "ashby", "smartrecruiters", "workday",
    "custom_api", "custom_html", "playwright", "oracle", "eightfold", "successfactors", "kula",
    "apple", "meta", "amazon", "avature", "manual", "atlassian", "phenom", "goldman", "trakstar",
}


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if not value:
        raise ValueError("Company name/id cannot produce an empty id")
    return value


def load_config(path: Path | str = CONFIG) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"companies": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("companies", [])
    if not isinstance(data["companies"], list):
        raise ValueError("config.companies must be a list")
    return data


def save_config(data: dict[str, Any], path: Path | str = CONFIG) -> None:
    """Atomically save YAML so an interrupted CLI command cannot corrupt config."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        f.write(rendered)
        temp_name = f.name
    Path(temp_name).replace(path)


def find_company(data: dict[str, Any], company_id: str) -> dict[str, Any] | None:
    return next((c for c in data.get("companies", []) if c.get("id") == company_id), None)


def next_rank(data: dict[str, Any]) -> int:
    ranks = [c.get("rank") for c in data.get("companies", []) if isinstance(c.get("rank"), int)]
    return max(ranks, default=0) + 1


def make_source(source_type: str = "auto", **kwargs) -> dict[str, Any]:
    if source_type not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported source type: {source_type}")
    src: dict[str, Any] = {"type": source_type}
    for key, value in kwargs.items():
        if value is not None:
            src[key] = value
    return src


def validate_config(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    for i, c in enumerate(data.get("companies", []), 1):
        prefix = f"row {i}"
        for key in ("id", "name", "career_url", "source"):
            if not c.get(key):
                errors.append(f"{prefix}: missing {key}")
        cid = c.get("id")
        if cid in seen_ids:
            errors.append(f"duplicate id: {cid}")
        if cid:
            seen_ids.add(cid)
        if "enabled" in c and not isinstance(c["enabled"], bool):
            errors.append(f"{cid or prefix}: enabled must be true/false")
        rank = c.get("rank")
        if rank is not None:
            if not isinstance(rank, int) or rank <= 0:
                errors.append(f"{cid or prefix}: rank must be a positive integer")
            elif rank in seen_ranks:
                errors.append(f"duplicate rank: {rank}")
            else:
                seen_ranks.add(rank)
        url = str(c.get("career_url", ""))
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"{cid or prefix}: invalid career_url")
        src = c.get("source") or {}
        stype = src.get("type") if isinstance(src, dict) else None
        if stype not in SUPPORTED_SOURCES:
            errors.append(f"{cid or prefix}: unsupported source type {stype!r}")
            continue
        required = {
            "greenhouse": ("board_token",),
            "lever": ("site",),
            "ashby": ("board_name",),
            "smartrecruiters": ("company_identifier",),
            "workday": ("host", "tenant", "site"),
            "oracle": ("host", "site_number"),
            "custom_api": ("endpoint", "field_mapping"),
            "custom_html": ("selectors",),
            "playwright": ("selectors",),
        }.get(stype, ())
        for key in required:
            if not src.get(key):
                errors.append(f"{cid or prefix}: source {stype} requires {key}")
    return errors
