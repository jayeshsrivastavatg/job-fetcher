from copy import deepcopy
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds


def html_of(r):
    try:
        data = r.json()
        if isinstance(data, dict): return data.get("results") or ""
        if isinstance(data, str): return data
    except Exception:
        pass
    return r.text


def snap(c):
    entry = (c.get("source") or {}).get("entry_url") or c["career_url"]
    p = urlparse(entry); origin = f"https://{p.netloc}"
    ids = set(); india = set()
    for page in range(1, 101):
        r = session().get(f"{origin}/en/search-jobs/results", params={"ActiveFacetID":0,"CurrentPage":page,"RecordsPerPage":50,"FacetType":0}, timeout=timeout_seconds(), headers={"X-Requested-With":"XMLHttpRequest","Accept":"*/*"})
        r.raise_for_status(); soup = BeautifulSoup(html_of(r), "html.parser")
        anchors = soup.select("a[href][data-job-id]")
        if not anchors: break
        for a in anchors:
            jid = str(a.get("data-job-id") or "").strip()
            if not jid: continue
            u = urljoin(origin, a.get("href") or "")
            if urlparse(u).netloc.casefold() != p.netloc.casefold(): continue
            ids.add(jid)
            box = a.find_parent("li") or a.parent
            loc = box.select_one(".job-location") if box else None
            if loc and "india" in loc.get_text(" ", strip=True).casefold(): india.add(jid)
        if len(anchors) < 50: break
    if not ids: raise RuntimeError("empty official witness")
    return ids, india


def main():
    cs=[c for c in load_config()["companies"] if c.get("enabled",True) and urlparse(str((c.get("source") or {}).get("entry_url") or c.get("career_url") or "")).path.rstrip("/").casefold().endswith("/search-jobs")]
    bad=0
    for c in cs:
        b,bi=snap(c); x=deepcopy(c); s=build_source(x); jobs=list(prefer_usable_jobs(s.fetch(x)) or []); by={str(j.external_id):j for j in jobs if j.external_id}; a,ai=snap(c)
        stable=b&a; allowed=b|a; ii=bi&ai; missing=stable-set(by); extras=set(by)-allowed
        no_loc=[i for i in ii if not str(getattr(by.get(i),"location","") or "").strip()]; no_jd=[i for i in ii if len(str(getattr(by.get(i),"description","") or "").strip())<120]
        ok=not missing and not extras and not no_loc and not no_jd
        print({"id":c["id"],"adapter":type(s).__name__,"before":len(b),"after":len(a),"production":len(by),"missing":len(missing),"extras":len(extras),"india":len(ii),"india_location":len(ii)-len(no_loc),"india_jd":len(ii)-len(no_jd),"passed":ok},flush=True); bad += not ok
    if not cs or bad: raise SystemExit(1)

if __name__ == "__main__": main()
