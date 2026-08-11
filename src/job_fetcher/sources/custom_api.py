from urllib.parse import urljoin

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


def get_path(obj, path, default=None):
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part, default)
    return cur


def s(v):
    return None if v is None else str(v)


class CustomApiSource(JobSource):
    def fetch(self, company):
        src = company["source"]
        method = src.get("method", "GET").upper()
        client = session()
        headers = {**src.get("headers", {})}
        kwargs = {"timeout": timeout_seconds(), "headers": headers}
        if method == "GET":
            r = client.get(src["endpoint"], params=src.get("params", {}), **kwargs)
        elif method == "POST":
            r = client.post(src["endpoint"], params=src.get("params", {}), json=src.get("body", {}), **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")
        r.raise_for_status()
        items = get_path(r.json(), src.get("jobs_path", "jobs"), [])
        if not isinstance(items, list):
            raise ValueError("jobs_path must resolve to a list")
        m = src["field_mapping"]
        out = []
        for x in items:
            u = s(get_path(x, m.get("job_url")))
            out.append(Job(
                company["id"], company["name"], "custom_api",
                s(get_path(x, m.get("external_id"))), s(get_path(x, m.get("title"))) or "",
                s(get_path(x, m.get("location"))), s(get_path(x, m.get("description"))),
                urljoin(company["career_url"], u) if u else None,
                s(get_path(x, m.get("posted_at"))), x if isinstance(x, dict) else {"value": x},
            ))
        return out
