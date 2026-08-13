from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests


TARGETS = {
    "rakuten_india": "https://rakuten.openings.co/rakuten/main.92406371525d74a6.js",
    "sony_tech_india": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/main.3fa7ec1d477903fd.js",
    "makemytrip": "https://careers.makemytrip.com/prod/bundle.js",
}

KEYWORDS = re.compile(
    r"(?:zwayam|api|job|jobs|career|opening|opportunit|requisition|search|vacan|tenant|client|domain|baseurl|host)",
    re.I,
)
URL_RE = re.compile(r"https?:\\?/\\?/[^\"'`\\\s<>]+", re.I)
STRING_RE = re.compile(r"(?P<q>[\"'`])(?P<s>(?:\\.|(?!\1).){1,700})(?P=q)", re.S)
SOURCE_MAP_RE = re.compile(r"sourceMappingURL=([^\s*]+)")


def _decode_literal(value: str) -> str:
    return value.replace("\\/", "/").replace("\\u002F", "/").replace("\\x2f", "/")


def _snippet(text: str, pos: int, before: int = 1000, after: int = 2400) -> str:
    return text[max(0, pos - before):min(len(text), pos + after)]


def probe(target: str) -> dict:
    url = TARGETS[target]
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 Chrome/131 Safari/537.36"})
    r = s.get(url, timeout=60)
    r.raise_for_status()
    text = r.text

    literals = []
    for m in STRING_RE.finditer(text):
        value = _decode_literal(m.group("s"))
        if KEYWORDS.search(value):
            literals.append(value)
            if len(literals) >= 2500:
                break

    urls = []
    for m in URL_RE.finditer(text):
        value = _decode_literal(m.group(0))
        if value not in urls:
            urls.append(value)
        if len(urls) >= 1000:
            break

    snippets = []
    patterns = [
        r"api\.zwayam",
        r"\.post\(",
        r"\.get\(",
        r"HttpClient",
        r"baseUrl",
        r"apiUrl",
        r"jobslist",
        r"jobview",
        r"job[_/-]?search",
        r"getjobs?",
        r"opportunit",
        r"requisition",
    ]
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            value = _snippet(text, match.start())
            fingerprint = value[:250]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            snippets.append({"pattern": pattern, "offset": match.start(), "text": value})
            if len(snippets) >= 350:
                break
        if len(snippets) >= 350:
            break

    source_map = None
    m = SOURCE_MAP_RE.search(text[-5000:])
    if m:
        source_map_url = urljoin(url, m.group(1).strip())
        try:
            mr = s.get(source_map_url, timeout=60)
            source_map = {
                "url": source_map_url,
                "status": mr.status_code,
                "bytes": len(mr.content),
            }
            if mr.ok and len(mr.content) <= 25_000_000:
                mp = mr.json()
                sources = list(mp.get("sources") or [])
                contents = list(mp.get("sourcesContent") or [])
                source_hits = []
                for idx, content in enumerate(contents):
                    if not content or not KEYWORDS.search(content):
                        continue
                    relevant = []
                    for km in KEYWORDS.finditer(content):
                        relevant.append(_snippet(content, km.start(), 500, 1500))
                        if len(relevant) >= 20:
                            break
                    source_hits.append({
                        "source": sources[idx] if idx < len(sources) else str(idx),
                        "snippets": relevant,
                    })
                    if len(source_hits) >= 100:
                        break
                source_map["source_hits"] = source_hits
        except Exception as exc:
            source_map = {"url": source_map_url, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "target": target,
        "url": url,
        "status": r.status_code,
        "bytes": len(r.content),
        "urls": urls,
        "interesting_literals": list(dict.fromkeys(literals))[:2500],
        "snippets": snippets,
        "source_map": source_map,
        "tail": text[-5000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = probe(args.target)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("TARGET", args.target, "STATUS", report["status"], "BYTES", report["bytes"])
    for value in report["urls"][:100]:
        print("URL", value[:1000])
    for value in report["interesting_literals"][:300]:
        print("LITERAL", value[:1500].replace("\n", " "))
    for row in report["snippets"][:80]:
        print("SNIPPET", row["pattern"], row["offset"], row["text"][:3500].replace("\n", " "))
    print("SOURCEMAP", json.dumps(report.get("source_map"), ensure_ascii=False)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
