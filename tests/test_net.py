import httpx
import pytest
import respx

from free_model_spider.net import request_with_retries


def make_client():
    return httpx.Client(base_url="https://api.example.test")


@respx.mock
def test_retries_on_500_then_succeeds():
    route = respx.get("https://api.example.test/x")
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"ok": True}),
    ]
    sleeps = []
    resp = request_with_retries(make_client(), "GET", "https://api.example.test/x",
                                sleep=sleeps.append)
    assert resp.json() == {"ok": True}
    assert sleeps == [1.0]


@respx.mock
def test_retries_on_429_and_transport_error():
    route = respx.get("https://api.example.test/y")
    route.side_effect = [
        httpx.Response(429),
        httpx.ConnectError("boom"),
        httpx.Response(200, json={}),
    ]
    sleeps = []
    resp = request_with_retries(make_client(), "GET", "https://api.example.test/y",
                                sleep=sleeps.append)
    assert resp.status_code == 200
    assert sleeps == [1.0, 2.0]


@respx.mock
def test_no_retry_on_404():
    route = respx.get("https://api.example.test/z").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(make_client(), "GET", "https://api.example.test/z",
                             sleep=lambda s: None)
    assert route.call_count == 1


@respx.mock
def test_exhausts_retries_then_raises():
    route = respx.get("https://api.example.test/w").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(make_client(), "GET", "https://api.example.test/w",
                             sleep=lambda s: None)
    assert route.call_count == 3


def test_retries_must_be_positive():
    with pytest.raises(ValueError):
        request_with_retries(make_client(), "GET", "https://api.example.test/x",
                             retries=0, sleep=lambda s: None)
