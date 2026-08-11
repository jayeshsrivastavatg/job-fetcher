from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource

class PlaywrightSource(JobSource):
    def fetch(self, company):
        src = company["source"]
        url = src.get("list_url") or company["career_url"]
        sel = src["selectors"]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(src.get("wait_for") or sel["card"], timeout=30000)
            self._load_more(page, src, sel)
            cards = page.locator(sel["card"])
            out = []
            for i in range(cards.count()):
                card = cards.nth(i)
                title = text(card, sel.get("title")) or ""
                location = text(card, sel.get("location"))
                href = attr(card, sel.get("link"), "href")
                job_url = urljoin(url, href) if href else None
                out.append(Job(company["id"], company["name"], "playwright", job_url,
                               title, location, None, job_url, None, None))
            browser.close()
            return out

    def _load_more(self, page, src, sel):
        selector = src.get("load_more_selector")
        if not selector: return
        for _ in range(int(src.get("max_load_more_clicks", 50))):
            btn = page.locator(selector)
            if btn.count() == 0 or not btn.first.is_visible(): break
            before = page.locator(sel["card"]).count()
            try:
                btn.first.click(timeout=5000)
                page.wait_for_timeout(750)
            except Exception:
                break
            if page.locator(sel["card"]).count() <= before: break

def text(card, selector):
    if not selector: return None
    n = card.locator(selector)
    return n.first.inner_text().strip() if n.count() else None

def attr(card, selector, name):
    if not selector: return None
    n = card.locator(selector)
    return n.first.get_attribute(name) if n.count() else None
