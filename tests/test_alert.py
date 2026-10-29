import json

import httpx
import respx

from free_model_spider.core.alert import run_link, send_alert

WEBHOOK_URL = "https://hook.example/alert"
NO_SLEEP = lambda _s: None  # noqa: E731


@respx.mock
def test_send_alert_skips_without_env(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    assert send_alert(httpx.Client(), "t", "c", sleep=NO_SLEEP) is False
    assert route.call_count == 0  # 未配置时应完全没有请求


@respx.mock
def test_send_alert_posts_title_and_content(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    assert send_alert(httpx.Client(), "标题X", "内容Y", sleep=NO_SLEEP) is True
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert set(body.keys()) == {"title", "content"}  # 严格两字段
    assert body["title"] == "标题X"
    assert body["content"] == "内容Y"


@respx.mock
def test_send_alert_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
    route = respx.post(WEBHOOK_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200)]
    )
    assert send_alert(httpx.Client(), "t", "c", sleep=NO_SLEEP) is True
    assert route.call_count == 3


@respx.mock
def test_send_alert_gives_up_after_retries(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
    assert send_alert(httpx.Client(), "t", "c", sleep=NO_SLEEP) is False  # 不抛
    assert route.call_count == 3


@respx.mock
def test_send_alert_swallows_transport_error(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
    respx.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert send_alert(httpx.Client(), "t", "c", sleep=NO_SLEEP) is False  # 不抛


@respx.mock
def test_send_alert_log_redacts_webhook_url(monkeypatch, capsys):
    """异常日志只打类型名与状态码：webhook URL（含 token）永不进日志。"""
    secret_url = "https://hook.example/alert?token=SECRET123"
    monkeypatch.setenv("ALERT_WEBHOOK_URL", secret_url)
    respx.post(secret_url).mock(return_value=httpx.Response(500))
    assert send_alert(httpx.Client(), "t", "c", sleep=NO_SLEEP) is False
    out = capsys.readouterr().out
    assert "SECRET123" not in out  # token 不得泄漏
    assert "https://hook.example/alert" not in out  # URL 本体也不得泄漏
    assert "[alert] 告警发送失败" in out  # 失败不能静默
    assert "HTTPStatusError" in out  # 只打印异常类型名
    assert "500" in out  # 并附 HTTP 状态码


def test_run_link_empty_outside_actions(monkeypatch):
    for var in ("GITHUB_ACTIONS", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    assert run_link() == ""


def test_run_link_builds_url_on_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    assert run_link() == "https://github.com/owner/repo/actions/runs/123456"


def test_run_link_partial_env_returns_empty(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert run_link() == ""
