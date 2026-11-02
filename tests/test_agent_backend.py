"""core/agent_backend.py 的行为规格：Dify Agent SSE 客户端（httpx-sse + respx）。"""

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from free_model_spider.core.agent_backend import (
    AgentAnswer,
    AgentStructuralError,
    AgentTransientError,
    DifyAgentBackend,
    build_agent_backend,
)

SSE_HEADERS = {"content-type": "text/event-stream"}
BASE = "https://dify.example.com/v1"
URL = f"{BASE}/chat-messages"
ENV = {"DIFY_API_BASE": BASE, "DIFY_API_TOKEN": "app-token",
       "CF_ACCESS_CLIENT_ID": "cid", "CF_ACCESS_CLIENT_SECRET": "csec"}


def _sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events)


def _mock_stream(*events: dict) -> httpx.Response:
    return httpx.Response(200, headers=SSE_HEADERS, content=_sse(*events))


@respx.mock
def test_ask_aggregates_until_message_end_and_sends_contract_request():
    route = respx.post(URL).mock(return_value=_mock_stream(
        {"event": "agent_message", "answer": "Claude"},
        {"event": "agent_message", "answer": " Opus 6"},
        {"event": "ping"},
        {"event": "message_end", "metadata": {"usage": {"total_tokens": 42}}},
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "app-token",
                               cf_client_id="cid", cf_client_secret="csec")

    answer = backend.ask("列出本周新增厂商")

    assert answer.text == "Claude Opus 6"
    assert answer.usage == {"total_tokens": 42}
    assert isinstance(answer, AgentAnswer)

    request = route.calls.last.request
    assert request.url.path == "/v1/chat-messages"
    assert json.loads(request.content) == {
        "inputs": {},
        "query": "列出本周新增厂商",
        "response_mode": "streaming",
        "user": "fms-vendors",
        "auto_generate_name": False,
    }
    assert request.headers["Authorization"] == "Bearer app-token"
    assert request.headers["CF-Access-Client-Id"] == "cid"
    assert request.headers["CF-Access-Client-Secret"] == "csec"


@respx.mock
def test_ask_without_cf_vars_omits_cf_headers():
    route = respx.post(URL).mock(return_value=_mock_stream({"event": "message_end"}))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    backend.ask("q")

    assert "CF-Access-Client-Id" not in route.calls.last.request.headers
    assert "CF-Access-Client-Secret" not in route.calls.last.request.headers


@respx.mock
def test_message_replace_overrides_buffer():
    respx.post(URL).mock(return_value=_mock_stream(
        {"event": "agent_message", "answer": "坏"},
        {"event": "message_replace", "answer": "好"},
        {"event": "message_end"},
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    assert backend.ask("q").text == "好"


@respx.mock
def test_error_event_raises_transient():
    respx.post(URL).mock(return_value=_mock_stream(
        {"event": "error", "status": 400, "code": "invalid_param", "message": "x"},
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    with pytest.raises(AgentTransientError) as ei:
        backend.ask("q")
    assert "invalid_param" in str(ei.value)  # 带 status/code/message


@respx.mock
def test_stream_without_message_end_raises_transient():
    respx.post(URL).mock(return_value=_mock_stream({"event": "agent_message", "answer": "a"}))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    with pytest.raises(AgentTransientError):
        backend.ask("q")


@pytest.mark.parametrize("status", [401, 403, 400, 404])
@respx.mock
def test_http_4xx_auth_errors_are_structural(status):
    respx.post(URL).mock(return_value=httpx.Response(status, json={"code": "invalid_param"}))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    with pytest.raises(AgentStructuralError) as ei:
        backend.ask("q")
    assert "invalid_param" in str(ei.value)  # 信息含响应体


@respx.mock
def test_first_attempt_503_then_success():
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(503),
        _mock_stream({"event": "agent_message", "answer": "ok"},
                     {"event": "message_end", "metadata": {"usage": {"total_tokens": 1}}}),
    ]
    rec: list[float] = []
    backend = DifyAgentBackend(httpx.Client(), BASE, "t", sleep=rec.append)

    answer = backend.ask("q")

    assert answer.text == "ok"
    assert rec == [30.0]
    assert len(route.calls) == 2


@respx.mock
def test_503_twice_raises_transient_no_more_retry():
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503), httpx.Response(503)]
    rec: list[float] = []
    backend = DifyAgentBackend(httpx.Client(), BASE, "t", sleep=rec.append)

    with pytest.raises(AgentTransientError):
        backend.ask("q")
    assert len(route.calls) == 2
    assert rec == [30.0]  # 只 sleep 一次，不再重试


def _first_event_then_raise() -> Iterator[bytes]:
    yield _sse({"event": "agent_message", "answer": "partial"})
    raise httpx.ReadTimeout("slow")


@respx.mock
def test_midstream_failure_is_transient_without_retry():
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(200, headers=SSE_HEADERS, content=_first_event_then_raise()),
    ]
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    with pytest.raises(AgentTransientError):
        backend.ask("q")
    assert len(route.calls) == 1  # 流中途失败不重试


@respx.mock
def test_wall_clock_limit_exceeded():
    respx.post(URL).mock(return_value=_mock_stream(
        {"event": "agent_message", "answer": "a"},
        {"event": "agent_message", "answer": "b"},
        {"event": "message_end"},
    ))
    ticks = [0.0, 0.0, 4000.0]  # start=0，首事件后 elapsed=0，第二事件前 elapsed=4000 > 3600
    backend = DifyAgentBackend(httpx.Client(), BASE, "t", clock=lambda: ticks.pop(0))

    with pytest.raises(AgentTransientError) as ei:
        backend.ask("q")
    assert "墙钟" in str(ei.value)


@respx.mock
def test_non_json_data_lines_are_ignored():
    respx.post(URL).mock(return_value=httpx.Response(
        200, headers=SSE_HEADERS,
        content=b"data: [DONE]\n\n"
                + _sse({"event": "agent_message", "answer": "好"}, {"event": "message_end"}),
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    assert backend.ask("q").text == "好"


@respx.mock
def test_non_dict_json_data_line_ignored():
    # data: 1 能 json.loads 成功但非 object，payload.get 会 AttributeError，须同样忽略
    respx.post(URL).mock(return_value=httpx.Response(
        200, headers=SSE_HEADERS,
        content=b"data: 1\n\n"
                + _sse({"event": "agent_message", "answer": "好"}, {"event": "message_end"}),
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    assert backend.ask("q").text == "好"


@respx.mock
def test_2xx_non_sse_body_is_transient_with_content_type():
    # httpx 的 text= 快捷方式固定 text/plain，这里显式给 CF 登录页的 text/html
    respx.post(URL).mock(return_value=httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, text="<html>login</html>"))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    with pytest.raises(AgentTransientError) as ei:
        backend.ask("q")
    assert "text/html" in str(ei.value)


@respx.mock
def test_build_agent_backend_validations(monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DIFY_API_BASE", BASE)
    with pytest.raises(AgentStructuralError):
        build_agent_backend(httpx.Client())  # 缺 DIFY_API_TOKEN

    monkeypatch.setenv("DIFY_API_TOKEN", "app-token")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cid")
    with pytest.raises(AgentStructuralError):
        build_agent_backend(httpx.Client())  # CF 只配 ID 不配 SECRET

    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "csec")
    monkeypatch.setenv("DIFY_API_BASE", BASE + "/")  # 带尾斜杠
    route = respx.post(URL).mock(return_value=_mock_stream(
        {"event": "message_end", "metadata": {"usage": {"total_tokens": 3}}}))
    backend = build_agent_backend(httpx.Client())
    assert isinstance(backend, DifyAgentBackend)

    answer = backend.ask("q")
    assert route.called  # 尾斜杠被容忍，请求仍打 /v1/chat-messages
    assert answer.usage == {"total_tokens": 3}


@respx.mock
def test_base_url_trailing_slash():
    route = respx.post(URL).mock(return_value=_mock_stream({"event": "message_end"}))
    backend = DifyAgentBackend(httpx.Client(), BASE + "/", "t")

    backend.ask("q")

    assert route.called  # 命中同一 URL


@respx.mock
def test_agent_thought_prints_progress_line(capsys):
    respx.post(URL).mock(return_value=_mock_stream(
        {"event": "agent_thought", "position": 1, "tool": "web_search",
         "tool_input": {"query": "free llm"}},
        {"event": "message_end"},
    ))
    backend = DifyAgentBackend(httpx.Client(), BASE, "t")

    backend.ask("q")

    out = capsys.readouterr().out
    assert "[dify] thought#1 tool=web_search input=" in out
