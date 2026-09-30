import os
import time
from collections.abc import Callable

import httpx

from free_model_spider.net import request_with_retries

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
DEFAULT_FALLBACKS = "nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        fallback_models: list[str],
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_models = fallback_models
        self.client = client or httpx.Client(timeout=120)
        self._sleep = sleep

    def complete(self, system: str, user: str) -> str:
        for model in [self.model, *self.fallback_models]:
            try:
                return self._complete_with_model(model, system, user)
            except (httpx.HTTPStatusError, httpx.TransportError, KeyError, IndexError):
                continue
        raise LLMError(f"所有模型均不可用: {self.model}, {self.fallback_models}")

    def _complete_with_model(self, model: str, system: str, user: str) -> str:
        resp = request_with_retries(
            self.client,
            "POST",
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            sleep=self._sleep,
        )
        return resp.json()["choices"][0]["message"]["content"]


def build_llm_client(client: httpx.Client | None = None) -> LLMClient | None:
    api_key = os.environ.get("LLM_API_KEY") or ""
    if not api_key:
        return None
    api_base = os.environ.get("LLM_API_BASE") or DEFAULT_API_BASE
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    fallbacks_raw = os.environ.get("LLM_FALLBACK_MODELS") or DEFAULT_FALLBACKS
    fallbacks = [m.strip() for m in fallbacks_raw.split(",") if m.strip()]
    return LLMClient(api_base=api_base, api_key=api_key, model=model,
                     fallback_models=fallbacks, client=client)
