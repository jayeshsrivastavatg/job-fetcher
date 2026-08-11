import os
import threading


class _DynamicBrowserGate:
    """A process-wide browser gate whose limit can change from the Settings UI."""
    def __init__(self):
        self._cond = threading.Condition()
        self._active = 0

    @staticmethod
    def _limit() -> int:
        try:
            return max(1, int(os.getenv("JOB_FETCHER_BROWSER_CONCURRENCY", "2")))
        except ValueError:
            return 2

    def __enter__(self):
        with self._cond:
            while self._active >= self._limit():
                self._cond.wait(timeout=1.0)
            self._active += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()
        return False


BROWSER_SEMAPHORE = _DynamicBrowserGate()
