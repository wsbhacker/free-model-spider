import os

import httpx

from free_model_spider.search.base import SearchProvider
from free_model_spider.search.brave import BraveSearch


def build_search_provider(client: httpx.Client | None = None) -> SearchProvider | None:
    api_key = os.environ.get("BRAVE_API_KEY") or ""
    if not api_key:
        return None
    return BraveSearch(api_key=api_key, client=client)
