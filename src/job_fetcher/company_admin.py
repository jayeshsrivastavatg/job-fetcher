from __future__ import annotations

import json
import threading
from copy import deepcopy
from typing import Any

from job_fetcher.config import (
    SUPPORTED_SOURCES,
    find_company,
    load_config,
    next_rank,
    save_config,
    slugify,
    validate_config,
)

_CONFIG_LOCK = threading.RLock()

SOURCE_FIELDS: dict[str, list[str]] = {
    "auto": ["entry_url"],
    "greenhouse": ["board_token", "entry_url"],
    "lever": ["site", "entry_url"],
    "ashby": ["board_name", "entry_url"],
    "smartrecruiters": ["company_identifier", "entry_url"],
    "workday": ["host", "tenant", "site", "entry_url"],
    "oracle": ["host", "site_number", "locale", "entry_url"],
    "eightfold": ["tenant", "locale", "canonical_base_url", "canonical_job_path_template", "entry_url"],
    "successfactors": ["entry_url", "max_pages", "max_jobs"],
    "kula": ["entry_url"],
    "apple": ["entry_url", "max_pages"],
    "meta": ["entry_url", "max_pages"],
    "amazon": ["entry_url", "max_pages"],
    "atlassian": ["entry_url", "max_pages"],
    "phenom": ["entry_url", "max_pages"],
    "goldman": ["entry_url", "max_pages"],
    "trakstar": ["entry_url", "max_pages"],
    "avature": ["entry_url"],
    "custom_api": ["endpoint", "field_mapping"],
    "custom_html": ["entry_url", "selectors"],
    "playwright": ["entry_url", "selectors"],
    "manual": ["reason", "entry_url"],
}


def source_schema() -> dict[str, Any]:
    return {"types": sorted(SUPPORTED_SOURCES), "fields": SOURCE_FIELDS}


def _normalize_source(source_type: str, career_url: str, source_config: dict[str, Any] | None,
                      existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if source_type not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported source type: {source_type}")
    old = deepcopy((existing or {}).get("source") or {})
    src = old if old.get("type") == source_type else {}
    src["type"] = source_type
    for k, v in (source_config or {}).items():
        if k == "type":
            continue
        if v in (None, ""):
            src.pop(k, None)
        else:
            src[k] = v
    if source_type == "auto":
        src["entry_url"] = src.get("entry_url") or career_url
    return src


def create_company(*, name: str, career_url: str, source_type: str = "auto",
                   source_config: dict[str, Any] | None = None, enabled: bool = True,
                   company_id: str | None = None, rank: int | None = None) -> dict[str, Any]:
    with _CONFIG_LOCK:
        data = load_config()
        cid = company_id or slugify(name)
        if find_company(data, cid):
            raise ValueError(f"Company id already exists: {cid}")
        row = {
            "id": cid,
            "rank": rank or next_rank(data),
            "name": name.strip(),
            "enabled": bool(enabled),
            "career_url": career_url.strip(),
            "source": _normalize_source(source_type, career_url.strip(), source_config),
            "research": {"status": "user_added", "provider_hint": source_type},
        }
        candidate = {**data, "companies": [*data["companies"], row]}
        errors = validate_config(candidate)
        if errors:
            raise ValueError("; ".join(errors))
        save_config(candidate)
        return deepcopy(row)


def update_company(company_id: str, *, name: str | None = None, career_url: str | None = None,
                   source_type: str | None = None, source_config: dict[str, Any] | None = None,
                   enabled: bool | None = None, rank: int | None = None) -> dict[str, Any]:
    with _CONFIG_LOCK:
        data = load_config()
        row = find_company(data, company_id)
        if not row:
            raise KeyError(company_id)
        if name is not None:
            row["name"] = name.strip()
        if career_url is not None:
            row["career_url"] = career_url.strip()
        if rank is not None:
            row["rank"] = int(rank)
        if enabled is not None:
            row["enabled"] = bool(enabled)
        chosen_type = source_type or (row.get("source") or {}).get("type", "auto")
        if source_type is not None or source_config is not None or career_url is not None:
            row["source"] = _normalize_source(chosen_type, row["career_url"], source_config, row)
            if chosen_type == "auto" and career_url is not None and not (source_config or {}).get("entry_url"):
                row["source"]["entry_url"] = row["career_url"]
        errors = validate_config(data)
        if errors:
            raise ValueError("; ".join(errors))
        save_config(data)
        return deepcopy(row)


def set_company_enabled(company_id: str, enabled: bool) -> dict[str, Any]:
    return update_company(company_id, enabled=enabled)


def source_config_json(company: dict[str, Any]) -> str:
    src = dict(company.get("source") or {})
    src.pop("type", None)
    return json.dumps(src, indent=2, ensure_ascii=False)
