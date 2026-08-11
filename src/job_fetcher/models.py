from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import Any

@dataclass
class Job:
    company_id: str
    company_name: str
    source_type: str
    external_id: str | None
    title: str
    location: str | None
    description: str | None
    job_url: str | None
    posted_at: str | None = None
    raw: dict[str, Any] | None = None

    def stable_external_id(self) -> str:
        if self.external_id:
            return str(self.external_id)
        key = "|".join([self.company_id, self.title or "", self.location or "", self.job_url or ""])
        return sha256(key.encode()).hexdigest()

    def to_dict(self):
        d = asdict(self)
        d["external_id"] = self.stable_external_id()
        return d
