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

    未配置 env 时打印跳过日志并返回 False；发送失败（连接错误、HTTP 错误、
    任何异常）打印中文日志并返回 False，绝不向调用方抛异常。
    """
    url = os.environ.get("ALERT_WEBHOOK_URL") or ""
    if not url:
        print("[alert] 未配置 ALERT_WEBHOOK_URL，跳过告警")
        return False
    try:
        request_with_retries(
            client,
            "POST",
            url,
            retries=3,
            json={"title": title, "content": content},
            sleep=sleep,
        )
        return True
    except Exception as exc:  # noqa: BLE001 —— fire-and-forget：告警失败绝不能影响主流程
        print(f"[alert] 告警发送失败：{exc!r}")
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
