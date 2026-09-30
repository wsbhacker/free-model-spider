import time
from collections.abc import Callable
from typing import ClassVar

import httpx

from free_model_spider.net import request_with_retries
from free_model_spider.search.base import SearchResult, SearchProvider

API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveSearch(SearchProvider):
    name: ClassVar[str] = "brave"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        min_interval: float = 1.1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)
        self.min_interval = min_interval
        self._sleep = sleep
        self._last_request_at: float | None = None

    def search(self, query: str, count: int = 5) -> list[SearchResult]:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_interval - elapsed
            if wait > 0:
                self._sleep(wait)
        resp = request_with_retries(
            self.client,
            "GET",
            API_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            params={"q": query, "count": count},
        )
        self._last_request_at = time.monotonic()
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in results[:count]
        ]
