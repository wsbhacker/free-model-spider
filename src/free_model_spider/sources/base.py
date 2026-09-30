from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field


class ModelRecord(BaseModel):
    source: str
    id: str
    name: str
    context_length: int | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    created: int | None = None
    description: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def page_url(self) -> str:
        return self.links.get("platform_page", "")


def sort_records(records: list[ModelRecord]) -> list[ModelRecord]:
    return sorted(records, key=lambda r: (r.id.lower(), r.id))


class Source(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]

    def __init__(self, client: httpx.Client):
        self.client = client
        self.last_errors: list[str] = []

    @abstractmethod
    def fetch_free_models(self) -> list[ModelRecord]: ...
