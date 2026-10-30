"""Webhook 故障告警：fire-and-forget，任何失败都不抛出。"""

import os
import time
from collections.abc import Callable

import httpx

from free_model_spider.net import request_with_retries


def send_alert(
    client: httpx.Client,
    title: str,
    content: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """向 ALERT_WEBHOOK_URL 发送告警通知。

    配置 ALERT_WEBHOOK_TOKEN 时附带 `x-token` 请求头（webhook 侧鉴权用，
    值按密钥管理：永不进仓库、不进日志）；未配置则不发该头。
    未配置 env 时打印跳过日志并返回 False；发送失败（连接错误、HTTP 错误、
    任何异常）打印中文日志并返回 False，绝不向调用方抛异常。
    """
    url = os.environ.get("ALERT_WEBHOOK_URL") or ""
    if not url:
        print("[alert] 未配置 ALERT_WEBHOOK_URL，跳过告警")
        return False
    token = os.environ.get("ALERT_WEBHOOK_TOKEN") or ""
    try:
        request_with_retries(
            client,
            "POST",
            url,
            retries=3,
            json={"title": title, "content": content},
            headers={"x-token": token} if token else None,
            sleep=sleep,
        )
        return True
    except Exception as exc:  # noqa: BLE001 —— fire-and-forget：告警失败绝不能影响主流程
        # 脱敏：异常消息/args 内嵌完整 webhook URL（含 token），永不进日志；
        # 只打印异常类型名与（若存在）HTTP 状态码。
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f" HTTP {status}" if status is not None else ""
        print(f"[alert] 告警发送失败：{type(exc).__name__}{detail}")
        return False


def run_link() -> str:
    """GitHub Actions 环境下返回本次运行链接，否则返回空串。"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return ""
    server = os.environ.get("GITHUB_SERVER_URL") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    run_id = os.environ.get("GITHUB_RUN_ID") or ""
    if not (server and repo and run_id):
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"
