import httpx
import pytest
import respx

from free_model_spider.llm import build_llm_client
from free_model_spider.llm.client import LLMClient, LLMError

URL = "https://openrouter.ai/api/v1/chat/completions"


def chat_content(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@respx.mock
def test_complete_returns_content():
    respx.post(URL).mock(return_value=httpx.Response(200, json=chat_content("你好")))
    llm = LLMClient(api_base="https://openrouter.ai/api/v1", api_key="k", model="m/a:free",
                    fallback_models=[])
    assert llm.complete("sys", "user") == "你好"


@respx.mock
def test_fallback_on_model_error():
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json=chat_content("来自回退模型")),
    ]
    sleeps = []
    llm = LLMClient(api_base="https://openrouter.ai/api/v1", api_key="k", model="m/a:free",
                    fallback_models=["m/b:free"], sleep=sleeps.append)
    assert llm.complete("sys", "user") == "来自回退模型"
    assert len(sleeps) == 2  # 主模型重试两次后退避


@respx.mock
def test_all_models_fail_raises_llm_error():
    respx.post(URL).mock(return_value=httpx.Response(429))
    llm = LLMClient(api_base="https://openrouter.ai/api/v1", api_key="k", model="m/a:free",
                    fallback_models=[], sleep=lambda s: None)
    with pytest.raises(LLMError):
        llm.complete("sys", "user")


@respx.mock
def test_malformed_200_body_falls_through_to_llm_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"choices": "not-a-list"}))
    llm = LLMClient(api_base="https://openrouter.ai/api/v1", api_key="k", model="m/a:free",
                    fallback_models=[], sleep=lambda s: None)
    with pytest.raises(LLMError):
        llm.complete("sys", "user")


def test_build_client_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert build_llm_client() is None
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = build_llm_client()
    assert client is not None
    assert client.model == "qwen/qwen3.8-27b:free"
