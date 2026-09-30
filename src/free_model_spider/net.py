import time
from collections.abc import Callable

import httpx


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 2.0,
    sleep: Callable[[float], None] | None = None,
    **kwargs,
) -> httpx.Response:
    """429/5xx/传输错误指数退避重试；其他 4xx 不重试直接抛。"""
    if retries < 1:
        raise ValueError("retries must be >= 1")
    sleeper = sleep or time.sleep
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            retriable = status is None or status == 429 or status >= 500
            if attempt == retries - 1 or not retriable:
                raise
            sleeper(delay)
            delay *= backoff
    raise RuntimeError("unreachable")
