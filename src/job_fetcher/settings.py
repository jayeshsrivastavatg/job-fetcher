from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "config" / "ui_settings.yaml"

DEFAULTS: dict[str, Any] = {
    "fetch_workers": 4,
    "http_timeout": 30.0,
    "retries": 3,
    "browser_fallback": True,
    "browser_workers": 2,
    "verification_drop_threshold": 0.80,
    "verify_sample_detail": True,
    "detail_timeout": 15.0,
}


def _coerce(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULTS)
    out.update(data or {})
    out["fetch_workers"] = max(1, min(32, int(out["fetch_workers"])))
    out["http_timeout"] = max(1.0, min(180.0, float(out["http_timeout"])))
    out["retries"] = max(0, min(10, int(out["retries"])))
    out["browser_fallback"] = bool(out["browser_fallback"])
    out["browser_workers"] = max(1, min(8, int(out["browser_workers"])))
    out["verification_drop_threshold"] = max(0.05, min(0.99, float(out["verification_drop_threshold"])))
    out["verify_sample_detail"] = bool(out["verify_sample_detail"])
    out["detail_timeout"] = max(1.0, min(120.0, float(out["detail_timeout"])))
    return out


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    raw = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return _coerce(raw if isinstance(raw, dict) else {})


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    current.update(values or {})
    data = _coerce(current)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, sort_keys=False)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=SETTINGS_PATH.parent, delete=False) as f:
        f.write(rendered)
        tmp = f.name
    Path(tmp).replace(SETTINGS_PATH)
    apply_settings(data)
    return data


def apply_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = _coerce(settings or load_settings())
    os.environ["JOB_FETCHER_HTTP_TIMEOUT"] = str(settings["http_timeout"])
    os.environ["JOB_FETCHER_RETRIES"] = str(settings["retries"])
    os.environ["JOB_FETCHER_BROWSER_CONCURRENCY"] = str(settings["browser_workers"])
    if settings["browser_fallback"]:
        os.environ.pop("JOB_FETCHER_DISABLE_BROWSER", None)
    else:
        os.environ["JOB_FETCHER_DISABLE_BROWSER"] = "1"
    return settings
