import httpx
import pytest
import respx

from free_model_spider.search import build_search_provider
from free_model_spider.search.brave import BraveSearch

BRAVE_JSON = {
    "web": {
        "results": [
            {"title": "T1", "url": "https://a.example/1", "description": "D1"},
            {"title": "T2", "url": "https://a.example/2", "description": "D2"},
        ]
    }
}


@respx.mock
def test_brave_search_returns_results():
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json=BRAVE_JSON)
    )
    provider = BraveSearch(api_key="k", min_interval=0.0)
    results = provider.search("some model vendor", count=2)
    assert [r.title for r in results] == ["T1", "T2"]
    assert route.calls.last.request.headers["X-Subscription-Token"] == "k"


@respx.mock
def test_brave_throttles_requests():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json=BRAVE_JSON)
    )
    sleeps = []
    provider = BraveSearch(api_key="k", min_interval=1.1, sleep=sleeps.append)
    provider.search("q1")
    provider.search("q2")
    assert len(sleeps) == 1  # 第二次请求前应等待


@respx.mock
def test_brave_failure_raises():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(500)
    )
    provider = BraveSearch(api_key="k", min_interval=0.0)
    with pytest.raises(httpx.HTTPStatusError):
        provider.search("q")


def test_build_provider_env(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert build_search_provider() is None
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    assert build_search_provider() is not None
