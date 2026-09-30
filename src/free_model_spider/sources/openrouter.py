from typing import Any, ClassVar

import httpx

from free_model_spider.net import request_with_retries
from free_model_spider.sources.base import ModelRecord, Source, sort_records
from free_model_spider.sources.registry import register

API_URL = "https://openrouter.ai/api/v1/models"
EXCLUDED_IDS = {"openrouter/free"}


def is_free(entry: dict[str, Any]) -> bool:
    pricing = entry.get("pricing") or {}
    return pricing.get("prompt") == "0" and pricing.get("completion") == "0"


def normalize(entry: dict[str, Any]) -> ModelRecord:
    arch = entry.get("architecture") or {}
    model_id = entry["id"]
    return ModelRecord(
        source="openrouter",
        id=model_id,
        name=entry.get("name") or model_id,
        context_length=entry.get("context_length"),
        input_modalities=list(arch.get("input_modalities") or []),
        output_modalities=list(arch.get("output_modalities") or []),
        created=entry.get("created"),
        description=entry.get("description") or "",
        links={
            "platform_page": f"https://openrouter.ai/{model_id}",
            "hugging_face": entry.get("hugging_face_id") or "",
        },
        raw={k: v for k, v in entry.items() if k != "description"},
    )


@register
class OpenRouterSource(Source):
    name: ClassVar[str] = "openrouter"
    display_name: ClassVar[str] = "OpenRouter"

    def fetch_free_models(self) -> list[ModelRecord]:
        resp = request_with_retries(self.client, "GET", API_URL, timeout=60)
        entries = resp.json()["data"]
        records: list[ModelRecord] = []
        errors: list[str] = []
        for e in entries:
            if e.get("id") in EXCLUDED_IDS or not is_free(e):
                continue
            try:
                records.append(normalize(e))
            except Exception as exc:
                errors.append(f"{e.get('id') or '<未知id>'}: {exc}")
        self.last_errors = errors
        return sort_records(records)
