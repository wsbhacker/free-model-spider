# 免费大模型日报（free-model-daily）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日抓取 OpenRouter 免费模型，与最近快照 diff 后生成"新增/移除/无变化"三段式 Markdown 日报（每平台一份，含中文介绍），随 git 提交留档。

**Architecture:** 单 CLI 命令 `fms run` 串起 fetch → diff → enrich（Brave 搜索 + LLM，逐模型降级）→ report → persist；数据源/搜索/LLM 三个可插拔抽象；GitHub Actions 每日调度并 commit。

**Tech Stack:** Python ≥3.14、uv、httpx、pydantic v2；测试 pytest + respx。

**Spec:** `docs/superpowers/specs/2026-09-30-free-model-daily-design.md`（本计划的所有行为细节以 spec 为准）

## Global Constraints

- Python `>=3.14`；运行时依赖仅 `httpx`、`pydantic`；开发依赖仅 `pytest`、`respx`
- 免费判定：`pricing.prompt == "0"` 且 `pricing.completion == "0"`（字符串比较）
- 排除 id：`openrouter/free`
- 日报路径：`reports/<source>-YYYY-MM-DD-免费模型清单.md`（平铺）；快照：`data/snapshots/<source>/YYYY-MM-DD.json`
- 日报日期时区 `Asia/Shanghai`；每天必出日报（无增删时一、二部分标"无变化"）；首日格式与无变化场景一致
- 段内按模型完整 id 排序：`key=(id.lower(), id)`；每个模型附平台页链接 `https://openrouter.ai/<id>`
- 介绍生成**无数量上限**；降级链：完整卡（搜索+LLM）→ 元数据卡（代码拼装）；diff 与日报结构永不因搜索/LLM 失败而缺失
- LLM 走 OpenAI 兼容端点，环境变量 `LLM_API_BASE`（默认 `https://openrouter.ai/api/v1`）、`LLM_API_KEY`、`LLM_MODEL`（默认 `qwen/qwen3.8-27b:free`）、`LLM_FALLBACK_MODELS`（默认 `nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free`）；任一变量为空字符串视同未配置
- 搜索环境变量 `BRAVE_API_KEY`，空视同未配置；请求间隔 ≥1.1s
- LLM 调用间 sleep 3s；429/5xx/网络错误指数退避重试 3 次（1s 起步、×2）
- git commit message 以 `[skip ci]` 结尾；Actions 环境下 commit 后自动 push
- token 只存环境变量/GitHub Secrets，永不写入仓库文件

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `src/free_model_spider/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: 可安装的包 `free_model_spider`；后续所有任务在此包内加模块

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "free-model-spider"
version = "0.1.0"
description = "每日追踪各平台免费大模型并生成 Markdown 日报"
requires-python = ">=3.14"
dependencies = [
    "httpx>=0.28",
    "pydantic>=2.9",
]

[project.scripts]
fms = "free_model_spider.cli:main"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "respx>=0.22",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/free_model_spider"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: 建包骨架**

```bash
mkdir -p src/free_model_spider/{sources,search,llm,core} tests
printf '__version__ = "0.1.0"\n' > src/free_model_spider/__init__.py
for d in sources search llm core; do touch src/free_model_spider/$d/__init__.py; done
```

- [ ] **Step 3: 写冒烟测试 `tests/test_smoke.py`**

```python
import free_model_spider


def test_package_importable():
    assert free_model_spider.__version__ == "0.1.0"
```

- [ ] **Step 4: 安装并运行测试**

```bash
uv sync
uv run pytest -v
```
Expected: 1 passed

- [ ] **Step 5: 写 .gitignore**

```
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests .gitignore uv.lock
git commit -m "chore: 项目脚手架（uv + pytest）"
```

---

### Task 2: HTTP 重试工具

**Files:**
- Create: `src/free_model_spider/net.py`
- Test: `tests/test_net.py`

**Interfaces:**
- Produces: `request_with_retries(client: httpx.Client, method: str, url: str, *, retries: int = 3, backoff: float = 2.0, sleep: Callable[[float], None] = time.sleep, **kwargs) -> httpx.Response` — 429/5xx/传输错误重试（1s 起步 ×backoff），其他 4xx 直接抛

- [ ] **Step 1: 写失败测试 `tests/test_net.py`**

```python
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
    respx.get("https://api.example.test/z").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(make_client(), "GET", "https://api.example.test/z",
                             sleep=lambda s: None)


@respx.mock
def test_exhausts_retries_then_raises():
    respx.get("https://api.example.test/w").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(make_client(), "GET", "https://api.example.test/w",
                             sleep=lambda s: None)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_net.py -v`
Expected: FAIL（`ModuleNotFoundError: free_model_spider.net`）

- [ ] **Step 3: 实现 `src/free_model_spider/net.py`**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_net.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/net.py tests/test_net.py
git commit -m "feat: HTTP 指数退避重试工具"
```

---

### Task 3: 数据模型与 OpenRouter 数据源

**Files:**
- Create: `src/free_model_spider/sources/base.py`
- Create: `src/free_model_spider/sources/registry.py`
- Create: `src/free_model_spider/sources/openrouter.py`
- Modify: `src/free_model_spider/sources/__init__.py`
- Test: `tests/test_openrouter_source.py`
- Test fixture: `tests/fixtures/openrouter_models_page.json`

**Interfaces:**
- Produces: `ModelRecord`（pydantic，字段 `source,id,name,context_length,input_modalities,output_modalities,created,description,links,raw`，属性 `page_url`）；`sort_records(records) -> list[ModelRecord]`；`Source` 抽象基类（`name`/`display_name` 类属性 + `fetch_free_models()` + 实例属性 `last_errors: list[str]` 收集坏记录）；`OpenRouterSource`（name=`"openrouter"`）；`register`/`create_source(name, client)`/`available_sources()`

- [ ] **Step 1: 写 fixture `tests/fixtures/openrouter_models_page.json`**

从真实响应裁剪而来（一条免费带 `:free`、一条免费无后缀、一条收费、一条被排除的 `openrouter/free`）：

```json
{
  "data": [
    {
      "id": "qwen/qwen3.8-27b:free",
      "name": "Qwen: Qwen3.8 27B (free)",
      "created": 1786722910,
      "context_length": 262144,
      "description": "Qwen3.8 27B is a free variant.",
      "hugging_face_id": null,
      "architecture": {
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"]
      },
      "pricing": {"prompt": "0", "completion": "0"}
    },
    {
      "id": "stealth/space-bunny-alpha",
      "name": "Space Bunny Alpha",
      "created": 1790174884,
      "context_length": 1000000,
      "description": "Stealth model free during preview.",
      "hugging_face_id": "some-org/space-bunny",
      "architecture": {
        "input_modalities": ["text"],
        "output_modalities": ["text"]
      },
      "pricing": {"prompt": "0", "completion": "0"}
    },
    {
      "name": "Broken Entry",
      "created": 1790000000,
      "context_length": 4096,
      "description": "Malformed entry without id.",
      "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
      "pricing": {"prompt": "0", "completion": "0"}
    },
    {
      "id": "openai/gpt-6.1-sol-pro",
      "name": "OpenAI: GPT-6.1 Sol Pro",
      "created": 1790702886,
      "context_length": 1050000,
      "description": "Paid flagship.",
      "hugging_face_id": null,
      "architecture": {
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"]
      },
      "pricing": {"prompt": "0.000002", "completion": "0.00001"}
    },
    {
      "id": "openrouter/free",
      "name": "OpenRouter: Free Router",
      "created": 1780000000,
      "context_length": 200000,
      "description": "Meta routing model, not a real model.",
      "hugging_face_id": null,
      "architecture": {
        "input_modalities": ["text"],
        "output_modalities": ["text"]
      },
      "pricing": {"prompt": "0", "completion": "0"}
    }
  ]
}
```

- [ ] **Step 2: 写失败测试 `tests/test_openrouter_source.py`**

```python
import json
import httpx
import respx

from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import ModelRecord, sort_records
from free_model_spider.sources.openrouter import is_free, normalize

PAGE = json.load(open("tests/fixtures/openrouter_models_page.json"))


def test_is_free():
    assert is_free(PAGE["data"][0])
    assert not is_free(PAGE["data"][2])


def test_normalize():
    r = normalize(PAGE["data"][0])
    assert isinstance(r, ModelRecord)
    assert r.source == "openrouter"
    assert r.id == "qwen/qwen3.8-27b:free"
    assert r.context_length == 262144
    assert r.input_modalities == ["text", "image"]
    assert r.page_url == "https://openrouter.ai/qwen/qwen3.8-27b:free"
    assert "description" not in r.raw


def test_sort_records_case_insensitive():
    a = ModelRecord(source="s", id="B/x", name="B")
    b = ModelRecord(source="s", id="a/y", name="A")
    assert [r.id for r in sort_records([a, b])] == ["a/y", "B/x"]


@respx.mock
def test_fetch_free_models_filters():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    src = create_source("openrouter", httpx.Client())
    records = src.fetch_free_models()
    assert [r.id for r in records] == [
        "qwen/qwen3.8-27b:free",
        "stealth/space-bunny-alpha",
    ]
    assert len(src.last_errors) == 1  # 缺 id 的坏记录被跳过并记录（spec §11）
    assert src.display_name == "OpenRouter"
    assert "openrouter" in available_sources()
```

- [ ] **Step 3: 运行确认失败**

Run: `uv run pytest tests/test_openrouter_source.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现 `sources/base.py`**

```python
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field


class ModelRecord(BaseModel):
    source: str
    id: str
    name: str
    context_length: int | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    created: int | None = None
    description: str = ""
    links: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def page_url(self) -> str:
        return self.links.get("platform_page", "")


def sort_records(records: list[ModelRecord]) -> list[ModelRecord]:
    return sorted(records, key=lambda r: (r.id.lower(), r.id))


class Source(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]

    def __init__(self, client: httpx.Client):
        self.client = client
        self.last_errors: list[str] = []

    @abstractmethod
    def fetch_free_models(self) -> list[ModelRecord]: ...
```

- [ ] **Step 5: 实现 `sources/registry.py`**

```python
from typing import TypeVar

import httpx

from free_model_spider.sources.base import Source

T = TypeVar("T", bound=type[Source])

REGISTRY: dict[str, type[Source]] = {}


def register(cls: T) -> T:
    REGISTRY[cls.name] = cls
    return cls


def create_source(name: str, client: httpx.Client) -> Source:
    return REGISTRY[name](client)


def available_sources() -> list[str]:
    return sorted(REGISTRY)
```

- [ ] **Step 6: 实现 `sources/openrouter.py`**

```python
from typing import Any, ClassVar

import httpx

from free_model_spider.net import request_with_retries
from free_model_spider.sources.base import ModelRecord, Source, sort_records
from free_model_spider.sources.registry import register

API_URL = "https://openrouter.ai/api/v1/models"
EXCLUDED_IDS = {"openrouter/free"}


def is_free(entry: dict[str, Any]) -> bool:
    pricing = entry.get("pricing") or {}
    return pricing.get("prompt") == "0" and pricing.get("completion") == "0"


def normalize(entry: dict[str, Any]) -> ModelRecord:
    arch = entry.get("architecture") or {}
    model_id = entry["id"]
    return ModelRecord(
        source="openrouter",
        id=model_id,
        name=entry.get("name") or model_id,
        context_length=entry.get("context_length"),
        input_modalities=list(arch.get("input_modalities") or []),
        output_modalities=list(arch.get("output_modalities") or []),
        created=entry.get("created"),
        description=entry.get("description") or "",
        links={
            "platform_page": f"https://openrouter.ai/{model_id}",
            "hugging_face": entry.get("hugging_face_id") or "",
        },
        raw={k: v for k, v in entry.items() if k != "description"},
    )


@register
class OpenRouterSource(Source):
    name: ClassVar[str] = "openrouter"
    display_name: ClassVar[str] = "OpenRouter"

    def fetch_free_models(self) -> list[ModelRecord]:
        resp = request_with_retries(self.client, "GET", API_URL, timeout=60)
        entries = resp.json()["data"]
        records: list[ModelRecord] = []
        errors: list[str] = []
        for e in entries:
            if e.get("id") in EXCLUDED_IDS or not is_free(e):
                continue
            try:
                records.append(normalize(e))
            except Exception as exc:
                errors.append(f"{e.get('id') or '<未知id>'}: {exc}")
        self.last_errors = errors
        return sort_records(records)
```

- [ ] **Step 7: 让 `sources/__init__.py` 触发注册**

```python
from free_model_spider.sources import openrouter  # noqa: F401  触发注册
from free_model_spider.sources.registry import available_sources, create_source, register

__all__ = ["available_sources", "create_source", "register"]
```

- [ ] **Step 8: 运行确认通过**

Run: `uv run pytest tests/test_openrouter_source.py -v`
Expected: 4 passed

- [ ] **Step 9: Commit**

```bash
git add src/free_model_spider/sources tests/fixtures tests/test_openrouter_source.py
git commit -m "feat: ModelRecord 模型与 OpenRouter 免费模型数据源"
```

---

### Task 4: 快照读写

**Files:**
- Create: `src/free_model_spider/core/snapshot.py`
- Test: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `ModelRecord`、`sort_records`
- Produces: `snapshot_path(data_dir: Path, source: str, date: str) -> Path`；`save_snapshot(records: list[ModelRecord], path: Path, source: str, date: str) -> None`；`load_snapshot(path: Path) -> list[ModelRecord]`（文件不存在抛 FileNotFoundError，由调用方先用 find_latest 判断）；`find_latest_snapshot(data_dir: Path, source: str, before_date: str) -> Path | None`（取日期 `< before_date` 的最新一份，无则 None）

- [ ] **Step 1: 写失败测试 `tests/test_snapshot.py`**

```python
from free_model_spider.core.snapshot import (
    find_latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from free_model_spider.sources.base import ModelRecord


def make_records():
    return [
        ModelRecord(source="openrouter", id="a/x:free", name="X"),
        ModelRecord(source="openrouter", id="b/y:free", name="Y"),
    ]


def test_save_and_load_roundtrip(tmp_path):
    path = snapshot_path(tmp_path, "openrouter", "2026-09-30")
    save_snapshot(make_records(), path, "openrouter", "2026-09-30")
    loaded = load_snapshot(path)
    assert [r.id for r in loaded] == ["a/x:free", "b/y:free"]


def test_save_sorts_by_id(tmp_path):
    path = snapshot_path(tmp_path, "openrouter", "2026-09-30")
    save_snapshot(list(reversed(make_records())), path, "openrouter", "2026-09-30")
    text = path.read_text(encoding="utf-8")
    assert text.index("a/x:free") < text.index("b/y:free")


def test_find_latest_snapshot(tmp_path):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        save_snapshot(make_records(), snapshot_path(tmp_path, "openrouter", day),
                      "openrouter", day)
    latest = find_latest_snapshot(tmp_path, "openrouter", "2026-10-01")
    assert latest is not None and latest.stem == "2026-09-30"
    assert find_latest_snapshot(tmp_path, "openrouter", "2026-09-28") is None
    assert find_latest_snapshot(tmp_path, "other", "2026-10-01") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `core/snapshot.py`**

```python
import json
from pathlib import Path

from free_model_spider.sources.base import ModelRecord, sort_records


def snapshot_path(data_dir: Path, source: str, date: str) -> Path:
    return data_dir / "snapshots" / source / f"{date}.json"


def save_snapshot(
    records: list[ModelRecord], path: Path, source: str, date: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "date": date,
        "count": len(records),
        "models": [r.model_dump(mode="json") for r in sort_records(records)],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_snapshot(path: Path) -> list[ModelRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ModelRecord.model_validate(m) for m in data["models"]]


def find_latest_snapshot(data_dir: Path, source: str, before_date: str) -> Path | None:
    directory = data_dir / "snapshots" / source
    if not directory.is_dir():
        return None
    candidates = sorted(
        p for p in directory.glob("*.json") if p.stem < before_date
    )
    return candidates[-1] if candidates else None
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/core/snapshot.py tests/test_snapshot.py
git commit -m "feat: 免费模型快照读写与最近快照查找"
```

---

### Task 5: Diff 规则

**Files:**
- Create: `src/free_model_spider/core/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `ModelRecord`
- Produces: `DiffResult`（pydantic：`added/removed/unchanged: list[ModelRecord]`，均按 id 排序）；`diff_models(today: list[ModelRecord], yesterday: list[ModelRecord]) -> DiffResult` — 以完整模型 id 为 key；`yesterday=[]` 时三段分别为 today/[]/[]（首日基线）

- [ ] **Step 1: 写失败测试 `tests/test_diff.py`**

```python
from free_model_spider.core.diff import diff_models
from free_model_spider.sources.base import ModelRecord


def rec(id_: str) -> ModelRecord:
    return ModelRecord(source="openrouter", id=id_, name=id_)


def test_added_removed_unchanged_sorted():
    today = [rec("c/z"), rec("a/x"), rec("b/y")]
    yesterday = [rec("a/x"), rec("d/w")]
    result = diff_models(today, yesterday)
    assert [r.id for r in result.added] == ["b/y", "c/z"]
    assert [r.id for r in result.removed] == ["d/w"]
    assert [r.id for r in result.unchanged] == ["a/x"]


def test_first_day_baseline():
    result = diff_models([rec("a/x")], [])
    assert [r.id for r in result.added] == ["a/x"]
    assert result.removed == [] and result.unchanged == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `core/diff.py`**

```python
from pydantic import BaseModel

from free_model_spider.sources.base import ModelRecord, sort_records


class DiffResult(BaseModel):
    added: list[ModelRecord]
    removed: list[ModelRecord]
    unchanged: list[ModelRecord]


def diff_models(today: list[ModelRecord], yesterday: list[ModelRecord]) -> DiffResult:
    today_by = {r.id: r for r in today}
    yesterday_by = {r.id: r for r in yesterday}
    added = sort_records([today_by[i] for i in today_by.keys() - yesterday_by.keys()])
    removed = sort_records([yesterday_by[i] for i in yesterday_by.keys() - today_by.keys()])
    unchanged = sort_records([today_by[i] for i in today_by.keys() & yesterday_by.keys()])
    return DiffResult(added=added, removed=removed, unchanged=unchanged)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_diff.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/core/diff.py tests/test_diff.py
git commit -m "feat: 免费模型增删 diff（完整 id 为 key，字母序）"
```

---

### Task 6: Brave 搜索适配器

**Files:**
- Create: `src/free_model_spider/search/base.py`
- Create: `src/free_model_spider/search/brave.py`
- Modify: `src/free_model_spider/search/__init__.py`
- Test: `tests/test_search_brave.py`

**Interfaces:**
- Produces: `SearchResult`（pydantic：`title,url,snippet`）；`SearchProvider` 抽象基类（`search(query, count=5) -> list[SearchResult]`，失败抛异常由调用方降级）；`BraveSearch(api_key, client=None, min_interval=1.1, sleep=time.sleep)`；`build_search_provider(client=None) -> SearchProvider | None`（`BRAVE_API_KEY` 为空返回 None）

- [ ] **Step 1: 写失败测试 `tests/test_search_brave.py`**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_search_brave.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `search/base.py`**

```python
from abc import ABC, abstractmethod

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, count: int = 5) -> list[SearchResult]: ...
```

- [ ] **Step 4: 实现 `search/brave.py`**

```python
import time
from collections.abc import Callable
from typing import ClassVar

import httpx

from free_model_spider.net import request_with_retries
from free_model_spider.search.base import SearchResult, SearchProvider

API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveSearch(SearchProvider):
    name: ClassVar[str] = "brave"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        min_interval: float = 1.1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)
        self.min_interval = min_interval
        self._sleep = sleep
        self._last_request_at: float | None = None

    def search(self, query: str, count: int = 5) -> list[SearchResult]:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.min_interval - elapsed
            if wait > 0:
                self._sleep(wait)
        resp = request_with_retries(
            self.client,
            "GET",
            API_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            params={"q": query, "count": count},
        )
        self._last_request_at = time.monotonic()
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in results[:count]
        ]
```

- [ ] **Step 5: 实现 `search/__init__.py`**

```python
import os

import httpx

from free_model_spider.search.base import SearchProvider
from free_model_spider.search.brave import BraveSearch


def build_search_provider(client: httpx.Client | None = None) -> SearchProvider | None:
    api_key = os.environ.get("BRAVE_API_KEY") or ""
    if not api_key:
        return None
    return BraveSearch(api_key=api_key, client=client)
```

- [ ] **Step 6: 运行确认通过**

Run: `uv run pytest tests/test_search_brave.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add src/free_model_spider/search tests/test_search_brave.py
git commit -m "feat: Brave 搜索适配器（限速 1.1s，未配 key 时整体禁用）"
```

---

### Task 7: LLM 客户端（OpenAI 兼容，可切私有服务）

**Files:**
- Create: `src/free_model_spider/llm/client.py`
- Modify: `src/free_model_spider/llm/__init__.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces: `LLMError(RuntimeError)`；`LLMClient(api_base, api_key, model, fallback_models, client=None, sleep=time.sleep)`，方法 `complete(system: str, user: str) -> str` — 依次尝试主模型与回退模型，单模型内 429/5xx 走 request_with_retries；全部失败抛 `LLMError`；`build_llm_client(client=None) -> LLMClient | None`（`LLM_API_KEY` 空返回 None；`LLM_API_BASE` 默认 `https://openrouter.ai/api/v1`；`LLM_MODEL` 默认 `qwen/qwen3.8-27b:free`；`LLM_FALLBACK_MODELS` 默认 `nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free`，逗号分隔）

- [ ] **Step 1: 写失败测试 `tests/test_llm_client.py`**

```python
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


def test_build_client_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert build_llm_client() is None
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = build_llm_client()
    assert client is not None
    assert client.model == "qwen/qwen3.8-27b:free"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_llm_client.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `llm/client.py`**

```python
import os
import time
from collections.abc import Callable

import httpx

from free_model_spider.net import request_with_retries

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
DEFAULT_FALLBACKS = "nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        fallback_models: list[str],
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_models = fallback_models
        self.client = client or httpx.Client(timeout=120)
        self._sleep = sleep

    def complete(self, system: str, user: str) -> str:
        for model in [self.model, *self.fallback_models]:
            try:
                return self._complete_with_model(model, system, user)
            except (httpx.HTTPStatusError, httpx.TransportError, KeyError, IndexError):
                continue
        raise LLMError(f"所有模型均不可用: {self.model}, {self.fallback_models}")

    def _complete_with_model(self, model: str, system: str, user: str) -> str:
        resp = request_with_retries(
            self.client,
            "POST",
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            sleep=self._sleep,
        )
        return resp.json()["choices"][0]["message"]["content"]


def build_llm_client(client: httpx.Client | None = None) -> LLMClient | None:
    api_key = os.environ.get("LLM_API_KEY") or ""
    if not api_key:
        return None
    api_base = os.environ.get("LLM_API_BASE") or DEFAULT_API_BASE
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    fallbacks_raw = os.environ.get("LLM_FALLBACK_MODELS") or DEFAULT_FALLBACKS
    fallbacks = [m.strip() for m in fallbacks_raw.split(",") if m.strip()]
    return LLMClient(api_base=api_base, api_key=api_key, model=model,
                     fallback_models=fallbacks, client=client)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_llm_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/llm tests/test_llm_client.py
git commit -m "feat: OpenAI 兼容 LLM 客户端（主模型+回退，可切私有服务）"
```

---

### Task 8: 介绍生成管线（无上限 + 降级链）

**Files:**
- Create: `src/free_model_spider/core/intro.py`
- Test: `tests/test_intro.py`

**Interfaces:**
- Consumes: `ModelRecord`、`SearchProvider.search()`、`LLMClient.complete()`、`LLMError`
- Produces: `IntroCard`（pydantic：`model_id, kind("full"|"metadata"), title, summary, highlights: list[str], caveat, sources: list[str]`）；`metadata_card(record) -> IntroCard`；`build_intro(record: ModelRecord, search: SearchProvider | None, llm: LLMClient | None) -> IntroCard` — 搜索失败静默降级（返回空结果），LLM 失败或两次 JSON 解析失败降级为 `metadata_card`

- [ ] **Step 1: 写失败测试 `tests/test_intro.py`**

```python
import httpx
import pytest
import respx

from free_model_spider.core.intro import IntroCard, build_intro, metadata_card
from free_model_spider.llm.client import LLMError
from free_model_spider.search.base import SearchProvider, SearchResult
from free_model_spider.sources.base import ModelRecord

REC = ModelRecord(
    source="openrouter",
    id="vendor/model-a:free",
    name="Vendor: Model A (free)",
    context_length=262144,
    input_modalities=["text"],
    output_modalities=["text"],
    created=1786722910,
    description="A great free model.",
)


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, results=None, error: Exception | None = None):
        self.results = results or []
        self.error = error
        self.queries: list[str] = []

    def search(self, query, count=5):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


class FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD_JSON = (
    '{"summary": "这是厂商 A 的免费模型。", '
    '"highlights": ["上下文 26 万", "支持文本"], '
    '"caveat": ""}'
)


def test_metadata_card_fields():
    card = metadata_card(REC)
    assert card.kind == "metadata"
    assert card.model_id == "vendor/model-a:free"
    assert "262,144" in card.summary or "262144" in card.summary
    assert card.sources == ["https://openrouter.ai/vendor/model-a:free"]


def test_build_intro_full_path():
    search = FakeSearch(results=[SearchResult(title="T", url="https://n.example/1", snippet="S")])
    llm = FakeLLM(replies=[GOOD_JSON])
    card = build_intro(REC, search, llm)
    assert card.kind == "full"
    assert card.summary == "这是厂商 A 的免费模型。"
    assert card.highlights == ["上下文 26 万", "支持文本"]
    assert "https://n.example/1" in card.sources
    assert search.queries == ["Vendor: Model A (free) vendor"]
    assert "只基于" in llm.calls[0][0]  # system prompt 硬约束


def test_build_intro_search_failure_still_full():
    search = FakeSearch(error=httpx.ConnectError("down"))
    llm = FakeLLM(replies=[GOOD_JSON])
    card = build_intro(REC, search, llm)
    assert card.kind == "full"
    assert card.sources == ["https://openrouter.ai/vendor/model-a:free"]


def test_build_intro_bad_json_then_retry_then_degrade():
    llm = FakeLLM(replies=["not json", "also not json"])
    card = build_intro(REC, FakeSearch(), llm)
    assert card.kind == "metadata"
    assert len(llm.calls) == 2  # 重试一次


def test_build_intro_llm_error_degrades():
    llm = FakeLLM(replies=[LLMError("boom")])
    card = build_intro(REC, FakeSearch(), llm)
    assert card.kind == "metadata"


def test_build_intro_no_llm_no_search():
    card = build_intro(REC, None, None)
    assert card.kind == "metadata"
    assert isinstance(card, IntroCard)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_intro.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `core/intro.py`**

```python
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from free_model_spider.llm.client import LLMClient, LLMError
from free_model_spider.search.base import SearchProvider, SearchResult
from free_model_spider.sources.base import ModelRecord

SHANGHAI = ZoneInfo("Asia/Shanghai")

_SYSTEM_PROMPT = (
    "你是大模型资讯编辑。请只基于给定材料（平台元数据与搜索结果摘要）整理该模型的中文介绍，"
    "禁止编造材料之外的信息；材料不足时在 caveat 字段说明。只输出一个 JSON 对象："
    '{"summary": "80-150字介绍", "highlights": ["要点1", "要点2"], "caveat": "信息局限或空字符串"}'
)


def metadata_card(record: ModelRecord) -> IntroCard:
    created = (
        datetime.fromtimestamp(record.created, tz=SHANGHAI).strftime("%Y-%m-%d")
        if record.created
        else "未知"
    )
    vendor = record.id.split("/")[0]
    summary = f"{vendor} 旗下模型，上下文窗口 {record.context_length or '未知'} tokens。"
    if record.description:
        excerpt = record.description[:200] + ("…" if len(record.description) > 200 else "")
        summary += f" 平台描述摘录：{excerpt}"
    return IntroCard(
        model_id=record.id,
        kind="metadata",
        title=record.name,
        summary=summary,
        highlights=[
            f"输入模态：{'、'.join(record.input_modalities) or '未知'}",
            f"输出模态：{'、'.join(record.output_modalities) or '未知'}",
            f"上架时间：{created}",
        ],
        caveat="未经过联网搜索与 LLM 整理，内容为平台元数据拼装。",
        sources=[record.page_url] if record.page_url else [],
    )


def _user_prompt(record: ModelRecord, results: list[SearchResult]) -> str:
    meta = record.model_dump_json(
        include={
            "id", "name", "context_length", "input_modalities",
            "output_modalities", "created", "description",
        }
    )
    lines = [f"平台元数据：{meta}", "搜索结果："]
    if results:
        lines += [f"- {r.title} | {r.url} | {r.snippet}" for r in results]
    else:
        lines.append("-（无，搜索不可用或未配置）")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        return None
    return data


def build_intro(
    record: ModelRecord,
    search: SearchProvider | None,
    llm: LLMClient | None,
) -> IntroCard:
    results: list[SearchResult] = []
    if search is not None:
        query = f"{record.name} {record.id.split('/')[0]}"
        try:
            results = search.search(query)
        except Exception:
            results = []

    card: IntroCard
    if llm is not None:
        parsed: dict | None = None
        for _ in range(2):
            try:
                text = llm.complete(_SYSTEM_PROMPT, _user_prompt(record, results))
            except LLMError:
                break
            parsed = _parse_llm_json(text)
            if parsed is not None:
                break
        if parsed is not None:
            source_urls = [record.page_url] if record.page_url else []
            source_urls += [r.url for r in results if r.url and r.url != record.page_url]
            card = IntroCard(
                model_id=record.id,
                kind="full",
                title=record.name,
                summary=parsed["summary"],
                highlights=[str(h) for h in parsed.get("highlights", [])][:5],
                caveat=str(parsed.get("caveat") or ""),
                sources=source_urls[:4],
            )
            return card

    card = metadata_card(record)
    return card
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_intro.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/core/intro.py tests/test_intro.py
git commit -m "feat: 介绍生成管线（搜索+LLM，两级降级，只基于材料）"
```

---

### Task 9: 日报渲染

**Files:**
- Create: `src/free_model_spider/core/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `ModelRecord.page_url`、`DiffResult`、`IntroCard`、`sort_records`
- Produces: `IndexEntry`（NamedTuple：`date, source, display, added: int, removed: int, path: str`）；`render_report(display_name: str, date: str, records: list[ModelRecord], diff: DiffResult, intros: dict[str, IntroCard], yesterday_date: str | None, snapshot_rel: str, anomalies: list[str] | None = None) -> str` — 三段式；无昨日快照或零增删时一、二部分标题为 `——无变化`；`anomalies` 非空时追加 `## ⚠️ 异常` 小节；段内 id 排序；移除条目带 `最后出现：{yesterday_date}`；`update_readme_index(readme: Path, entries: list[IndexEntry]) -> None` — 维护 `<!-- fms-index:start/end -->` 标记内的表格（日期倒序，按 `(date, display)` 去重替换），标记不存在则追加章节，文件不存在则创建

- [ ] **Step 1: 写失败测试 `tests/test_report.py`**

```python
from pathlib import Path

from free_model_spider.core.diff import diff_models
from free_model_spider.core.intro import metadata_card
from free_model_spider.core.report import render_report, update_readme_index
from free_model_spider.sources.base import ModelRecord


def rec(id_: str, name: str = "", ctx: int | None = 262144) -> ModelRecord:
    return ModelRecord(
        source="openrouter", id=id_, name=name or id_, context_length=ctx,
        input_modalities=["text"], output_modalities=["text"],
    )


def build(added_ids, removed_ids, unchanged_ids):
    today = [rec(i) for i in added_ids + unchanged_ids]
    yesterday = [rec(i) for i in removed_ids + unchanged_ids]
    diff = diff_models(today, yesterday)
    intros = {r.id: metadata_card(r) for r in diff.added}
    return today, yesterday, diff, intros


def test_change_day_report():
    today, _, diff, intros = build(["a/new:free"], ["z/old:free"], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "data/snapshots/openrouter/2026-09-30.json")
    assert text.startswith("# OpenRouter 免费模型清单 2026-09-30")
    assert "免费 2 个（2026-09-29：+1 / -1）" in text
    assert "## 🆕 新增 (1)" in text
    assert "### a/new:free" in text
    assert "https://openrouter.ai/a/new:free" in text
    assert "## 🗑️ 移除 (1)" in text
    assert "[z/old:free](https://openrouter.ai/z/old:free)（最后出现：2026-09-29）" in text
    assert "## 📋 无变化 (1)" in text
    assert "[m/mid:free](https://openrouter.ai/m/mid:free)" in text
    assert text.rstrip().endswith("data/snapshots/openrouter/2026-09-30.json")


def test_no_change_day_marks_sections():
    today, _, diff, intros = build([], [], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json")
    assert "## 🆕 新增——无变化" in text
    assert "## 🗑️ 移除——无变化" in text
    assert "免费 1 个（2026-09-29：+0 / -0）" in text


def test_first_day_same_format_as_no_change():
    today = [rec("m/mid:free")]
    diff = diff_models(today, [])
    intros = {r.id: metadata_card(r) for r in diff.added}
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         None, "snap.json")
    assert "## 🆕 新增——无变化" in text
    assert "## 🗑️ 移除——无变化" in text
    assert "## 📋 无变化 (1)" in text


def test_sections_sorted_alphabetically():
    today, _, diff, intros = build(["c/z:free", "a/x:free"], [], [])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json")
    assert text.index("### a/x:free") < text.index("### c/z:free")


def test_anomalies_section():
    today, _, diff, intros = build([], [], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json",
                         anomalies=["<未知id>: KeyError('id')"])
    assert "## ⚠️ 异常 (1)" in text
    assert "- <未知id>: KeyError('id')" in text


def test_update_readme_index(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n\n## 日报索引\n\n<!-- fms-index:start -->\n"
                      "| 日期 | 平台 | 新增 | 移除 | 日报 |\n|---|---|---|---|---|\n"
                      "<!-- fms-index:end -->\n", encoding="utf-8")
    update_readme_index(readme, [
        ("2026-09-30", "openrouter", "OpenRouter", 2, 1,
         "reports/openrouter-2026-09-30-免费模型清单.md"),
        ("2026-09-29", "openrouter", "OpenRouter", 0, 0,
         "reports/openrouter-2026-09-29-免费模型清单.md"),
    ])
    text = readme.read_text(encoding="utf-8")
    assert "reports/openrouter-2026-09-30-免费模型清单.md" in text
    assert text.index("2026-09-30") < text.index("2026-09-29")  # 日期倒序

    # 同日重跑：替换旧行而不是追加
    update_readme_index(readme, [
        ("2026-09-30", "openrouter", "OpenRouter", 3, 0,
         "reports/openrouter-2026-09-30-免费模型清单.md"),
    ])
    text = readme.read_text(encoding="utf-8")
    assert text.count("| 2026-09-30 | OpenRouter |") == 1
    assert "+3" in text and "+2" not in text
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `core/report.py`**

```python
import re
from pathlib import Path
from typing import NamedTuple

from free_model_spider.core.diff import DiffResult
from free_model_spider.core.intro import IntroCard
from free_model_spider.sources.base import ModelRecord, sort_records

INDEX_START = "<!-- fms-index:start -->"
INDEX_END = "<!-- fms-index:end -->"


class IndexEntry(NamedTuple):
    date: str
    source: str
    display: str
    added: int
    removed: int
    path: str


def _fmt_context(record: ModelRecord) -> str:
    return f"{record.context_length:,}" if record.context_length else "未知"


def _fmt_modalities(record: ModelRecord) -> str:
    inputs = "、".join(record.input_modalities) or "未知"
    outputs = "、".join(record.output_modalities) or "未知"
    return f"{inputs}→{outputs}"


def _added_section(records: list[ModelRecord], intros: dict[str, IntroCard]) -> list[str]:
    lines = [f"## 🆕 新增 ({len(records)})" if records else "## 🆕 新增——无变化"]
    if not records:
        lines.append("- 无")
        return lines
    for r in records:
        card = intros.get(r.id) or IntroCard(model_id=r.id, kind="metadata", title=r.name)
        lines.append(f"### {r.id}")
        lines.append(f"- 名称：{card.title}")
        lines.append(f"- 介绍：{card.summary}")
        if card.highlights:
            lines.append(f"- 要点：{'；'.join(card.highlights)}")
        if card.caveat:
            lines.append(f"- 注意：{card.caveat}")
        links = [f"[平台页]({r.page_url})"] if r.page_url else []
        links += [f"<{url}>" for url in card.sources if url != r.page_url]
        if links:
            lines.append(f"- 链接：{' ｜ '.join(links)}")
    return lines


def _removed_section(records: list[ModelRecord], yesterday_date: str | None) -> list[str]:
    lines = [f"## 🗑️ 移除 ({len(records)})" if records else "## 🗑️ 移除——无变化"]
    if not records:
        lines.append("- 无")
        return lines
    for r in records:
        link = f"[{r.id}]({r.page_url})" if r.page_url else r.id
        last_seen = f"（最后出现：{yesterday_date}）" if yesterday_date else ""
        lines.append(f"- {link}{last_seen}")
    return lines


def _unchanged_section(records: list[ModelRecord]) -> list[str]:
    lines = [f"## 📋 无变化 ({len(records)})"]
    if not records:
        lines.append("- 无")
        return lines
    lines += ["| 模型 | 名称 | 上下文 | 模态 |", "|---|---|---|---|"]
    for r in records:
        link = f"[{r.id}]({r.page_url})" if r.page_url else r.id
        lines.append(f"| {link} | {r.name} | {_fmt_context(r)} | {_fmt_modalities(r)} |")
    return lines


def render_report(
    display_name: str,
    date: str,
    records: list[ModelRecord],
    diff: DiffResult,
    intros: dict[str, IntroCard],
    yesterday_date: str | None,
    snapshot_rel: str,
    anomalies: list[str] | None = None,
) -> str:
    header = f"# {display_name} 免费模型清单 {date}"
    if yesterday_date:
        stats = (
            f"> 免费 {len(records)} 个（{yesterday_date}"
            f"：+{len(diff.added)} / -{len(diff.removed)}）"
        )
    else:
        stats = f"> 免费 {len(records)} 个（首日基线）"
    lines = [header, stats, "", *_added_section(sort_records(diff.added), intros),
             "", *_removed_section(sort_records(diff.removed), yesterday_date),
             "", *_unchanged_section(sort_records(diff.unchanged))]
    if anomalies:
        lines += ["", f"## ⚠️ 异常 ({len(anomalies)})"]
        lines += [f"- {a}" for a in anomalies]
    lines += ["", "---", f"快照：{snapshot_rel}", ""]
    return "\n".join(lines)


_ROW_RE = re.compile(r"^\| (\d{4}-\d{2}-\d{2}) \| (\S+) \|")


def update_readme_index(readme: Path, entries: list[IndexEntry]) -> None:
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
    else:
        text = "# free-model-spider\n"
    start = text.find(INDEX_START)
    end = text.find(INDEX_END)
    rows: list[tuple[str, str, str]] = []  # (date, 显示名, raw_line)
    if start != -1 and end != -1:
        for line in text[start + len(INDEX_START) : end].splitlines():
            m = _ROW_RE.match(line)
            if m:
                rows.append((m.group(1), m.group(2), line))
    else:
        text = text.rstrip("\n") + "\n\n## 日报索引\n\n"
        start, end = -1, -1
    today_keys = {(e.date, e.display) for e in entries}
    rows = [r for r in rows if (r[0], r[1]) not in today_keys]
    for e in entries:
        rows.append((e.date, e.source,
                     f"| {e.date} | {e.display} | +{e.added} | -{e.removed} | "
                     f"[链接]({e.path}) |"))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    block = "\n".join(
        ["| 日期 | 平台 | 新增 | 移除 | 日报 |", "|---|---|---|---|---|",
         *[r[2] for r in rows]]
    )
    if start != -1 and end != -1:
        text = text[: start + len(INDEX_START)] + "\n" + block + "\n" + text[end:]
    else:
        text = text + f"{INDEX_START}\n{block}\n{INDEX_END}\n"
    readme.write_text(text, encoding="utf-8")
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_report.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/free_model_spider/core/report.py tests/test_report.py
git commit -m "feat: 三段式日报渲染与 README 日报索引维护"
```

---

### Task 10: CLI 编排

**Files:**
- Create: `src/free_model_spider/cli.py`
- Modify: `src/free_model_spider/sources/__init__.py`（无需改动则跳过）
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 前面全部任务的公开接口
- Produces: `main(argv: list[str] | None = None) -> int`；子命令 `run`，参数 `--date`（默认北京时间今天）、`--sources`（逗号分隔，默认全部）、`--data-dir`（默认 `data`）、`--reports-dir`（默认 `reports`）、`--readme`（默认 `README.md`）、`--no-commit`；fetch 失败异常上抛（非零退出）；首日基线模式（无昨日快照 → diff 全为存量、不生成介绍，格式同"无变化"）；坏记录经 `source.last_errors` 进日报"⚠️ 异常"小节；新增模型逐一 `build_intro`，LLM 调用间 `time.sleep(3)`；有 commit 权限路径下 `git add <data> <reports> README.md` + `git commit -m "docs: 免费模型日报 <date> [skip ci]"`（nothing to commit 视为成功，其他 git 故障非零退出），`GITHUB_ACTIONS=true` 时追加 `git push`

- [ ] **Step 1: 写失败测试 `tests/test_cli.py`**

```python
import subprocess
from pathlib import Path

import httpx
import respx

from free_model_spider.cli import main

PAGE_DAY1 = {
    "data": [
        {"id": "a/x:free", "name": "X", "created": 1786722910,
         "context_length": 1000, "description": "dx",
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "pricing": {"prompt": "0", "completion": "0"}},
    ]
}
PAGE_DAY2 = {
    "data": [
        {"id": "b/y:free", "name": "Y", "created": 1786800000,
         "context_length": 2000, "description": "dy",
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "pricing": {"prompt": "0", "completion": "0"}},
    ]
}


def _mock_api(page):
    return respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=page)
    )


def _git(repo: Path, *args: str):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "README.md").write_text("# free-model-spider\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")


@respx.mock
def test_first_then_second_day(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    route = _mock_api(PAGE_DAY1)
    assert main(["run", "--date", "2026-09-30", "--no-commit"]) == 0
    report1 = Path("reports/openrouter-2026-09-30-免费模型清单.md").read_text("utf-8")
    assert "首日基线" in report1
    assert "## 🆕 新增——无变化" in report1
    assert Path("data/snapshots/openrouter/2026-09-30.json").exists()

    route.side_effect = [httpx.Response(200, json=PAGE_DAY2)]
    assert main(["run", "--date", "2026-10-01", "--no-commit"]) == 0
    report2 = Path("reports/openrouter-2026-10-01-免费模型清单.md").read_text("utf-8")
    assert "免费 1 个（2026-09-30：+1 / -1）" in report2
    assert "### b/y:free" in report2
    assert "## 🗑️ 移除 (1)" in report2
    assert "[a/x:free]" in report2
    readme = Path("README.md").read_text("utf-8")
    assert readme.count("| 2026-10-01 | OpenRouter |") == 1
    assert readme.count("| 2026-09-30 | OpenRouter |") == 1


@respx.mock
def test_commit_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    _mock_api(PAGE_DAY1)
    assert main(["run", "--date", "2026-09-30"]) == 0
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], check=True,
        capture_output=True, text=True,
    ).stdout
    assert "[skip ci]" in log.splitlines()[0]


@respx.mock
def test_fetch_failure_propagates(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避等待
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(500)
    )
    try:
        main(["run", "--date", "2026-09-30", "--no-commit"])
        raised = False
    except httpx.HTTPStatusError:
        raised = True
    assert raised
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL（cli 不存在）

- [ ] **Step 3: 实现 `cli.py`**

```python
import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from free_model_spider.core.diff import DiffResult, diff_models
from free_model_spider.core.intro import build_intro
from free_model_spider.core.report import IndexEntry, render_report, update_readme_index
from free_model_spider.core.snapshot import (
    find_latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from free_model_spider.llm.client import build_llm_client
from free_model_spider.search import build_search_provider
from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import sort_records

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _git_commit(date: str, data_dir: Path, reports_dir: Path, readme: Path) -> None:
    subprocess.run(["git", "add", str(data_dir), str(reports_dir), str(readme)],
                   check=True)
    result = subprocess.run(
        ["git", "commit", "-m", f"docs: 免费模型日报 {date} [skip ci]"],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        if b"nothing to commit" in result.stderr:
            print("git commit: nothing to commit, skip")
            return
        result.check_returncode()  # 真实 git 故障 → 非零退出，Actions 红灯（spec §11）
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "push"], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fms")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--date", default=None, help="YYYY-MM-DD，默认北京时间今天")
    run.add_argument("--sources", default=None, help="逗号分隔，默认全部注册的数据源")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--reports-dir", type=Path, default=Path("reports"))
    run.add_argument("--readme", type=Path, default=Path("README.md"))
    run.add_argument("--no-commit", action="store_true")
    args = parser.parse_args(argv)

    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60)
    names = args.sources.split(",") if args.sources else available_sources()
    search = build_search_provider(client)
    llm = build_llm_client(client)
    entries: list[IndexEntry] = []

    for name in names:
        source = create_source(name, client)
        records = source.fetch_free_models()
        latest = find_latest_snapshot(args.data_dir, name, date)
        if latest is None:
            # 首日基线：不产生新增/移除，全部计入存量，格式同"无变化"（spec §6）
            diff = DiffResult(added=[], removed=[], unchanged=sort_records(records))
        else:
            diff = diff_models(records, load_snapshot(latest))
        intros = {}
        for record in diff.added:
            intros[record.id] = build_intro(record, search, llm)
            if llm is not None:
                time.sleep(3)
        spath = snapshot_path(args.data_dir, name, date)
        save_snapshot(records, spath, name, date)
        report_rel = args.reports_dir / f"{name}-{date}-免费模型清单.md"
        text = render_report(
            source.display_name, date, records, diff, intros,
            latest.stem if latest else None, spath.as_posix(),
            anomalies=source.last_errors or None,
        )
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        report_rel.write_text(text, encoding="utf-8")
        entries.append(IndexEntry(
            date=date, source=name, display=source.display_name,
            added=len(diff.added), removed=len(diff.removed),
            path=report_rel.as_posix(),
        ))
        print(f"{name}: 免费 {len(records)}（+{len(diff.added)} / -{len(diff.removed)}）"
              f" -> {report_rel}")

    update_readme_index(args.readme, entries)
    if not args.no_commit:
        _git_commit(date, args.data_dir, args.reports_dir, args.readme)
    return 0
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -v`
Expected: 全部通过

- [ ] **Step 6: Commit**

```bash
git add src/free_model_spider/cli.py tests/test_cli.py
git commit -m "feat: fms run CLI 编排（fetch→diff→enrich→report→persist）"
```

---

### Task 11: GitHub Actions 工作流与项目文档

**Files:**
- Create: `.github/workflows/daily.yml`
- Create: `.env.example`
- Modify: `README.md`（补全使用说明，保留 `fms-index` 标记）

**Interfaces:**
- Consumes: `fms run`（默认带 commit）、环境变量约定（Global Constraints）
- Produces: 每日 08:30（北京时间）自动运行、可手动触发的工作流

- [ ] **Step 1: 写 `.github/workflows/daily.yml`**

```yaml
name: daily-free-model-report

on:
  schedule:
    - cron: "30 0 * * *"  # UTC 00:30 = 北京 08:30
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: daily-free-model-report
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          uv sync --frozen

      - name: Configure git identity
        run: |
          git config user.name "free-model-bot"
          git config user.email "free-model-bot@users.noreply.github.com"

      - name: Run daily report
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          BRAVE_API_KEY: ${{ secrets.BRAVE_API_KEY }}
          LLM_API_BASE: ${{ vars.LLM_API_BASE }}
          LLM_MODEL: ${{ vars.LLM_MODEL }}
          LLM_FALLBACK_MODELS: ${{ vars.LLM_FALLBACK_MODELS }}
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          uv run fms run
```

注：`vars.*` 未配置时 GitHub 传空字符串，代码内 `os.environ.get(...) or 默认值` 已兼容。

- [ ] **Step 2: 写 `.env.example`**

```
# 复制为 .env（已 gitignore）并填入；留空或删除 = 功能降级
LLM_API_KEY=sk-or-...
LLM_API_BASE=https://openrouter.ai/api/v1
LLM_MODEL=qwen/qwen3.8-27b:free
LLM_FALLBACK_MODELS=nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free
BRAVE_API_KEY=
```

- [ ] **Step 3: 补全 `README.md`**（标题与介绍自拟，必须包含以下要点与固定章节）

```markdown
# free-model-spider

每日抓取各平台免费大模型，与最近快照对比生成「新增 / 移除 / 无变化」三段式
Markdown 日报，由 GitHub Actions 每日北京时间 08:30 自动运行并提交。
当前数据源：OpenRouter（架构上支持扩展更多平台，各平台独立追踪、不去重）。

## 日报索引

<!-- fms-index:start -->
| 日期 | 平台 | 新增 | 移除 | 日报 |
|---|---|---|---|---|
<!-- fms-index:end -->

## 使用

- GitHub Actions：在仓库 Settings → Secrets and variables → Actions 配置
  `LLM_API_KEY`（必需，OpenRouter 免费 key 即可）与 `BRAVE_API_KEY`（可选）。
- 本地运行：

  ```bash
  uv sync
  cp .env.example .env   # 填入 key
  uv run fms run --no-commit
  ```

- 扩展新平台：实现 `Source` 接口（`src/free_model_spider/sources/base.py`）
  并 `@register`，日报与快照自动多出该平台。
```

- [ ] **Step 4: 验证 workflow 语法**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/daily.yml'))" 2>/dev/null || uv run python -c "import yaml" 2>/dev/null || echo "无 pyyaml，人工检查缩进即可"`
Expected: 无异常输出（或人工确认缩进）

- [ ] **Step 5: Commit**

```bash
git add .github .env.example README.md
git commit -m "ci: 每日调度工作流与使用文档"
```

---

### Task 12: 本地真实冒烟（联网，一次性验证）

**Files:**
- Create（运行产物，不提交）: 本地 `data/`、`reports/`、`README.md` 索引

**Interfaces:**
- Consumes: 全部真实外部服务（OpenRouter API；LLM/Brave 按本机 env 可用性降级）

- [ ] **Step 1: 配置本地密钥**

```bash
cp .env.example .env
# 编辑 .env：填入 LLM_API_KEY（OpenRouter 免费 key）与 BRAVE_API_KEY
set -a; source .env; set +a
```

- [ ] **Step 2: 首日基线运行**

```bash
uv run fms run --no-commit --date 2026-09-30
```
Expected: 控制台输出 `openrouter: 免费 N（+0 / -0） -> reports/openrouter-2026-09-30-免费模型清单.md`；`data/snapshots/openrouter/2026-09-30.json` 与日报文件生成；日报为三段式，首日基线模式下"🆕 新增——无变化"、"🗑️ 移除——无变化"，"📋 无变化 (N)" 列出全部 N 个模型（spec §6：首日格式与无变化场景一致，不生成介绍卡）

- [ ] **Step 3: 人工检查日报**

打开 `reports/openrouter-2026-09-30-免费模型清单.md`，确认：三段齐全（一、二部分为"无变化"，属首日基线预期）、第三部分列全部模型、段内字母序、每个模型带 `https://openrouter.ai/<id>` 链接、README 索引表已更新。次日再跑一次（不传 `--date`）应产生真实 diff 与介绍卡。

- [ ] **Step 4: 清理冒烟产物并提交（如需要）**

```bash
git checkout -- README.md 2>/dev/null || true
rm -rf data reports   # 或保留作为首日基线提交
git add -A && git commit -m "chore: 冒烟通过" || echo "无变更"
```

注：若保留 `data/`、`reports/` 作为真实基线，则直接提交它们。
