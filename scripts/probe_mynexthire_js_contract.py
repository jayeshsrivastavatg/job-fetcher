from __future__ import annotations

import json
import re
from pathlib import Path

import requests


URL = "https://swiggy.mynexthire.com/employer/ui/js/jobboard/careers.js"
NEEDLES = [
    r"getRequisition",
    r"requisitionPageClass",
    r"reqId",
    r"careersFactory",
    r"/careers/",
    r"/requisition",
    r"job.*detail",
    r"req.*detail",
]


def main() -> int:
    r = requests.get(URL, timeout=45, headers={"User-Agent": "Mozilla/5.0 Chrome/131 Safari/537.36"})
    r.raise_for_status()
    text = r.text
    report = {"url": URL, "bytes": len(text), "matches": []}
    seen = set()
    for needle in NEEDLES:
        for match in re.finditer(needle, text, re.I):
            lo = max(0, match.start() - 1800)
            hi = min(len(text), match.end() + 3500)
            snippet = text[lo:hi]
            key = snippet[:250]
            if key in seen:
                continue
            seen.add(key)
            report["matches"].append({"needle": needle, "offset": match.start(), "snippet": snippet})
            if len(report["matches"]) >= 100:
                break
        if len(report["matches"]) >= 100:
            break

    # Also extract every literal that looks like a relative HTTP endpoint so the
    # factory definition cannot hide behind a minified variable name.
    report["endpoint_literals"] = list(dict.fromkeys(
        m.group(1)
        for m in re.finditer(r"[\"']([^\"']*(?:career|requis|req|job)[^\"']*)[\"']", text, re.I)
        if len(m.group(1)) < 500
    ))[:500]

    Path("reports/phase4").mkdir(parents=True, exist_ok=True)
    Path("reports/phase4/mynexthire-js-contract.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("BYTES", len(text), "MATCHES", len(report["matches"]))
    for literal in report["endpoint_literals"]:
        print("LITERAL", literal)
    for row in report["matches"][:30]:
        print("MATCH", row["needle"], row["offset"])
        print(row["snippet"][:5200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
