from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


TARGETS = {
    "rakuten_india": {
        "entry": "https://rakuten.openings.co/rakuten/jobslist",
        "extra": [
            "https://rakuten.openings.co/robots.txt",
            "https://rakuten.openings.co/sitemap.xml",
            "https://api.zwayam.com/",
            "https://api.zwayam.com/swagger/index.html",
            "https://api.zwayam.com/swagger/v1/swagger.json",
            "https://api.zwayam.com/openapi.json",
        ],
    },
    "sony_tech_india": {
        "entry": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
        "extra": [
            "https://careers.sonyindiasoftware.co.in/robots.txt",
            "https://careers.sonyindiasoftware.co.in/sitemap.xml",
            "https://api.zwayam.com/",
            "https://api.zwayam.com/swagger/index.html",
            "https://api.zwayam.com/swagger/v1/swagger.json",
            "https://api.zwayam.com/openapi.json",
        ],
    },
    "makemytrip": {
        "entry": "https://careers.makemytrip.com/prod/",
        "extra": [
            "https://careers.makemytrip.com/robots.txt",
            "https://careers.makemytrip.com/sitemap.xml",
            "https://careers.makemytrip.com/sitemap_index.xml",
            "https://careers.makemytrip.com/prod/sitemap.xml",
            "https://careers.makemytrip.com/prod/opportunities",
        ],
    },
}

INTERESTING = re.compile(
    r"(?:api\.zwayam|apic\d*\.zwayam|jobview|jobslist|job[_/-]?search|getjobs?|jobdetails?|career|opportunit|requisition|opening|/api/|graphql|sitemap|baseurl|apidomain|axios|fetch\s*\()",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")
QUOTED_PATH_RE = re.compile(r"[\"']([^\"']{1,240}(?:job|career|opportun|requisition|opening|api)[^\"']{0,240})[\"']", re.I)


def _clip(text: str, limit: int = 50000) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _requests_get(session: requests.Session, url: str) -> dict:
    try:
        r = session.get(url, timeout=(10, 45), allow_redirects=True)
        return {
            "ok": True,
            "status": r.status_code,
            "final_url": r.url,
            "headers": dict(r.headers),
            "content_length": len(r.content),
            "text": _clip(r.text),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _curl_http1(url: str) -> dict:
    try:
        proc = subprocess.run(
            [
                "curl", "--http1.1", "-L", "--compressed", "--max-time", "35",
                "-A", "Mozilla/5.0 Chrome/131 Safari/537.36",
                "-sS", "-w", "\n__PHASE4_HTTP__:%{http_code}:%{url_effective}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        marker = "\n__PHASE4_HTTP__:"
        body, _, tail = proc.stdout.rpartition(marker)
        status = None
        final_url = None
        if tail:
            pieces = tail.split(":", 1)
            try:
                status = int(pieces[0])
            except Exception:
                status = None
            final_url = pieces[1] if len(pieces) > 1 else None
        return {
            "returncode": proc.returncode,
            "status": status,
            "final_url": final_url,
            "stderr": _clip(proc.stderr, 6000),
            "content_length": len(body.encode("utf-8", errors="ignore")),
            "text": _clip(body),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _scripts(base_url: str, html: str) -> list[str]:
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        html_base = soup.find("base", href=True)
        resolution_base = urljoin(base_url, html_base.get("href")) if html_base else base_url
        return list(dict.fromkeys(
            urljoin(resolution_base, tag.get("src"))
            for tag in soup.find_all("script", src=True)
            if tag.get("src")
        ))[:150]
    except Exception:
        return []


def _interesting_evidence(text: str) -> dict:
    urls = [u.rstrip(")],.;") for u in URL_RE.findall(text or "") if INTERESTING.search(u)]
    paths = []
    for match in QUOTED_PATH_RE.finditer(text or ""):
        value = match.group(1)
        if len(value) <= 500:
            paths.append(value)
        if len(paths) >= 300:
            break
    snippets = []
    for match in INTERESTING.finditer(text or ""):
        snippets.append((text[max(0, match.start() - 700):match.end() + 1800]).replace("\n", " "))
        if len(snippets) >= 120:
            break
    return {
        "urls": list(dict.fromkeys(urls))[:300],
        "quoted_paths": list(dict.fromkeys(paths))[:300],
        "snippets": snippets,
    }


def discover(target: str) -> dict:
    cfg = TARGETS[target]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })

    report = {"target": target, "entry": cfg["entry"], "documents": {}, "scripts": {}}
    urls = [cfg["entry"], *cfg.get("extra", [])]
    for url in urls:
        req = _requests_get(session, url)
        curl = None
        if not req.get("ok") or int(req.get("status") or 0) >= 400:
            curl = _curl_http1(url)
        report["documents"][url] = {"requests": req, "curl_http1": curl}
        status = req.get("status") if req.get("ok") else None
        print("DOC", target, status, req.get("content_length"), url, req.get("error") or "")

    entry_row = report["documents"][cfg["entry"]]
    entry_text = ((entry_row.get("requests") or {}).get("text") or "")
    if not entry_text:
        entry_text = ((entry_row.get("curl_http1") or {}).get("text") or "")
    base = ((entry_row.get("requests") or {}).get("final_url") or
            (entry_row.get("curl_http1") or {}).get("final_url") or cfg["entry"])
    entry_host = urlparse(base).netloc.lower()

    for script_url in _scripts(base, entry_text):
        row = _requests_get(session, script_url)
        text = row.get("text") or ""
        same_origin = urlparse(script_url).netloc.lower() == entry_host
        evidence = _interesting_evidence(text)
        # Record every first-party app bundle, even if it is minified and our keyword
        # matcher misses its endpoint strings. This prevents a useful main bundle from
        # disappearing from the diagnostic report merely because it uses compressed names.
        if same_origin or evidence["urls"] or evidence["quoted_paths"] or evidence["snippets"]:
            report["scripts"][script_url] = {
                "status": row.get("status"),
                "error": row.get("error"),
                "content_length": row.get("content_length"),
                "same_origin": same_origin,
                "evidence": evidence,
            }
            print("SCRIPT", row.get("status"), row.get("content_length"), script_url)
            for u in evidence["urls"][:30]:
                print("URL", u)
            for path in evidence["quoted_paths"][:30]:
                print("PATH", path[:500])

    report["entry_evidence"] = _interesting_evidence(entry_text)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = discover(args.target)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
