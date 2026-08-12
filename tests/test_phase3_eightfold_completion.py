from __future__ import annotations

from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource
from job_fetcher.sources.eightfold_pcsx_exhaustive import EightfoldPcsxExhaustiveSource


def _row(job_id: str) -> dict:
    return {"id": job_id, "name": f"Job {job_id}"}


def test_morgan_stanley_uses_half_page_overlap(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    source._full_passes = 2
    starts: list[int] = []

    def fake_page(origin: str, domain: str, start: int):
        starts.append(start)
        ids = [str(i) for i in range(start, min(start + 10, 11))]
        return [_row(job_id) for job_id in ids], 11

    monkeypatch.setattr(source, "_page", fake_page)

    rows, evidence = source.enumerate_rows({
        "id": "morgan_stanley",
        "source": {
            "entry_url": "https://morganstanley.eightfold.ai/careers?domain=morganstanley.com&hl=en",
        },
    })

    assert set(rows) == {str(i) for i in range(11)}
    assert starts == [0, 5, 10, 0, 5, 10]
    assert evidence["page_stride"] == 5
    assert evidence["pagination_exhausted"] is True


def test_exact_source_retries_detail_hydration(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    source._detail_attempts = 3
    attempts = {"count": 0}

    def flaky_detail(self, origin: str, domain: str, position_id: str):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return None
        return {"jobDescription": "Complete JD"}

    monkeypatch.setattr(EightfoldPcsxSource, "_detail", flaky_detail)
    monkeypatch.setattr("job_fetcher.sources.eightfold_pcsx_exhaustive.time.sleep", lambda _seconds: None)

    detail = source._detail("https://example.eightfold.ai", "example.com", "123")

    assert attempts["count"] == 3
    assert detail == {"jobDescription": "Complete JD"}
