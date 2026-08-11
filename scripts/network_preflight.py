#!/usr/bin/env python3
"""Check basic DNS/TLS reachability before diagnosing individual scrapers."""
import json
import socket
import time
from pathlib import Path

import requests

HOSTS = [
    "jobs.uber.com",
    "www.google.com",
    "api.lever.co",
    "api.smartrecruiters.com",
]

rows = []
for host in HOSTS:
    t = time.monotonic()
    try:
        ip = socket.gethostbyname(host)
        rows.append({"host": host, "dns": "ok", "ip": ip, "seconds": round(time.monotonic()-t, 3)})
    except Exception as e:
        rows.append({"host": host, "dns": "failed", "error": f"{type(e).__name__}: {e}", "seconds": round(time.monotonic()-t, 3)})

http = None
try:
    r = requests.get("https://www.google.com/", timeout=5)
    http = {"status": "ok", "http_status": r.status_code}
except Exception as e:
    http = {"status": "failed", "error": f"{type(e).__name__}: {e}"}

result = {"dns": rows, "https": http}
Path("logs").mkdir(exist_ok=True)
Path("logs/environment-network-preflight.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
