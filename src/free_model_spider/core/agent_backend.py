"""Dify Agent SSE 客户端：厂商新鲜度问答改问 Dify Agent。

错误分两类：
- 暂态（AgentTransientError）：限流/5xx/传输中断/流中途失败/error 事件等，可稍后重试；
- 结构性（AgentStructuralError）：配置缺失、鉴权失败（401/403/400/404）等，重试无意义。
"""

import os
import time
from collections.abc import Callable
from typing import NamedTuple, Protocol

import httpx
import httpx_sse

DIFY_USER = "fms-vendors"
# 单位秒；按 httpx.Timeout 位参数顺序 connect/read/write/pool，读放宽到 300s（长推理流式输出）
STREAM_TIMEOUT = httpx.Timeout(15, read=300, write=30, pool=15)
WALL_CLOCK_LIMIT_SECONDS = 3600.0  # 整体墙钟上限：超限转暂态，防止 Agent 失控悬挂
RETRY_DELAY_SECONDS = 30.0  # 首字节前暂态失败的重试间隔

STRUCTURAL_STATUSES = (400, 401, 403, 404)  # 这些 HTTP 状态为结构性失败


class AgentAnswer(NamedTuple):
    """一次 Agent 问答的结果。"""

    text: str  # agent_message 聚合 / message_replace 覆盖后的最终回答全文
    usage: dict | None  # message_end.metadata.usage


class AgentTransientError(RuntimeError):
    """暂态失败：可重试。"""


class AgentStructuralError(RuntimeError):
    """结构性失败：配置/鉴权问题，重试无意义。"""


class AgentBackend(Protocol):
    def ask(self, query: str) -> AgentAnswer: ...


class DifyAgentBackend:
    """Dify Chat-App `POST /chat-messages`（streaming）阻塞式客户端。"""

    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        token: str,
        cf_client_id: str | None = None,
        cf_client_secret: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.base_url = base_url
        self.token = token
        self.cf_client_id = cf_client_id
        self.cf_client_secret = cf_client_secret
        self._sleep = sleep
        self._clock = clock

    def ask(self, query: str) -> AgentAnswer:
        url = self.base_url.rstrip("/") + "/chat-messages"
        body = {
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "user": DIFY_USER,
            "auto_generate_name": False,
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        if self.cf_client_id and self.cf_client_secret:
            headers["CF-Access-Client-Id"] = self.cf_client_id
            headers["CF-Access-Client-Secret"] = self.cf_client_secret

        start = self._clock()
        for attempt in range(2):  # 仅"首字节前"失败重试 1 次
            in_stream = False  # 是否已越过"打开+状态检查"，进入事件迭代
            buffer: list[str] = []
            usage: dict | None = None
            try:
                # Accept/Cache-Control 由 httpx-sse 自动附加
                with httpx_sse.connect_sse(
                    self.client, "POST", url, json=body, headers=headers,
                    timeout=STREAM_TIMEOUT,
                ) as event_source:
                    status = event_source.response.status_code
                    if status in STRUCTURAL_STATUSES:
                        event_source.response.read()
                        raise AgentStructuralError(
                            f"Dify HTTP {status}：{event_source.response.text[:200]}"
                        )
                    if status == 429 or status >= 500:
                        if attempt >= 1:
                            raise AgentTransientError(f"Dify HTTP {status}（重试后仍失败）")
                        self._sleep(RETRY_DELAY_SECONDS)
                        continue
                    in_stream = True
                    for sse in event_source.iter_sse():
                        elapsed = self._clock() - start
                        if elapsed > WALL_CLOCK_LIMIT_SECONDS:
                            raise AgentTransientError(
                                f"Dify 流式墙钟超限：{elapsed:.0f}s > {WALL_CLOCK_LIMIT_SECONDS:.0f}s"
                            )
                        try:
                            payload = sse.json()
                        except ValueError:  # 非 JSON data 行（如 [DONE]、无 data 的 ping）忽略
                            continue
                        if not isinstance(payload, dict):  # JSON 非 object（如 data: 1）忽略
                            continue
                        event = payload.get("event")
                        if event in ("agent_message", "message"):
                            buffer.append(payload.get("answer") or "")
                        elif event == "message_replace":
                            buffer = [payload.get("answer") or ""]
                        elif event == "agent_thought":
                            print(f"[dify] thought#{payload.get('position')} "
                                  f"tool={payload.get('tool')} "
                                  f"input={str(payload.get('tool_input'))[:80]}")
                        elif event == "message_end":
                            usage = (payload.get("metadata") or {}).get("usage")
                            elapsed = self._clock() - start
                            print(f"[dify] 完成：耗时 {elapsed:.0f}s usage={usage}")
                            return AgentAnswer(text="".join(buffer), usage=usage)
                        elif event == "error":
                            raise AgentTransientError(
                                f"Dify error 事件：status={payload.get('status')} "
                                f"code={payload.get('code')} message={payload.get('message')}"
                            )
                        # ping 与未知事件忽略
                raise AgentTransientError("Dify 流耗尽未见 message_end")
            except (httpx.TransportError, httpx_sse.SSEError) as exc:
                if in_stream:  # 流中途失败不重试
                    raise AgentTransientError(f"Dify 流中途失败：{exc}") from exc
                if attempt >= 1:  # 首字节前失败且已重试过
                    raise AgentTransientError(f"Dify 首字节前失败（重试后仍失败）：{exc}") from exc
                self._sleep(RETRY_DELAY_SECONDS)
        raise AgentTransientError("unreachable")  # 循环每条路径要么 return 要么 raise


def build_agent_backend(client: httpx.Client) -> DifyAgentBackend:
    """从环境变量构建 DifyAgentBackend；配置缺失或 CF 两变量只配一个 → 结构性错误。"""
    base_url = os.environ.get("DIFY_API_BASE") or ""
    token = os.environ.get("DIFY_API_TOKEN") or ""
    cf_client_id = os.environ.get("CF_ACCESS_CLIENT_ID") or ""
    cf_client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET") or ""
    if not base_url or not token:
        raise AgentStructuralError("缺少 DIFY_API_BASE / DIFY_API_TOKEN 环境变量")
    if bool(cf_client_id) != bool(cf_client_secret):
        raise AgentStructuralError("CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET 必须同时配置")
    return DifyAgentBackend(client, base_url, token,
                            cf_client_id=cf_client_id, cf_client_secret=cf_client_secret)
