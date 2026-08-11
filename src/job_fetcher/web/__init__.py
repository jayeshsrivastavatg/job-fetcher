from job_fetcher.web.app import app
from job_fetcher.web.history_web import router as history_router
from job_fetcher.web.reset_web import router as reset_router

app.include_router(history_router)
app.include_router(reset_router)

__all__ = ["app"]
