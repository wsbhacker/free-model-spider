# 厂商新鲜度 v4（Dify Agent 监测）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `fms vendors` 从"人工维护模型清单 + 管线提取日期"改为"Dify Agent 定期监测新模型 + 一次性基线 + 状态增量"。

**Architecture:** 新增三个纯函数/客户端模块（agent_backend、vendor_state、vendor_agent），随后一次性切换（重写 config/report/cli、删除 v3 提取管线三模块）、最后改 workflow。Task 1–3 期间旧代码不动，每个 commit 全量测试绿；Task 4 是唯一的切换 commit。

**Tech Stack:** Python 3.14、httpx ≥0.28、**httpx-sse ≥0.4（新增）**、pydantic ≥2.9、pytest + respx、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-11-02-vendor-agent-freshness-design.md`（本计划不重复其理由，只定文件、签名与测试；执行者需同时读 spec）

## Global Constraints

- 状态目录为 `data/vendor-releases/`（spec §5）。注意：v3 代码写的是 `data/vendor-models/`（vendor_stats.py:55-61），本计划统一改为 vendor-releases。
- 状态 JSON 顶层键名是 `"schema": 4`（spec §5）；pydantic 字段名用 `schema_version` + `alias="schema"`（`schema` 会遮蔽 `BaseModel.schema()`），dump 用 `by_alias=True`。
- 暂态错误不改写状态；结构性错误中止整个运行且不写当日基线（spec §2 目标 4、§8.1）。
- 退出码：结构性/顶层异常或 `failed` 非空 → 1；仅暂态/拒收 → 0（spec §8.1 第 6 步）。
- 告警标题字面量：`发现新模型`、`大模型新鲜度厂商异常`、`大模型新鲜度日报失败`（spec §10）。
- Dify 请求体：`{"inputs": {}, "query": ..., "response_mode": "streaming", "user": "fms-vendors", "auto_generate_name": false}`，不传 `conversation_id`（spec §6.2）。
- 环境变量名：`DIFY_API_BASE`（含 `/v1`，末尾 `/` 容忍）、`DIFY_API_TOKEN`、`CF_ACCESS_CLIENT_ID`、`CF_ACCESS_CLIENT_SECRET`（两个 CF 变量要么都配要么都不配）。
- 超时：`httpx.Timeout(connect=15, read=300, write=30, pool=15)`；整体墙钟 900 秒；首字节前暂态失败重试 1 次、间隔 30 秒；流中途失败不重试（spec §6.2）。
- 仓库纪律：暂存按路径精确 `git add`，禁 `git add -A`；绝不 `git push`。
- 每个 Task 结束跑 `uv run pytest -q` 全绿再 commit（Task 4 除外：其内部步骤自带目标测试，commit 前同样全量绿）。

## Review Focus

spec 未逐条点名、但最容易在真实数据上出事的五类输入，及其钉死测试（已并入所属 Task）：

1. agent 给的日期不是 ISO（"2026/11/01"、"November 1, 2026"、"2026-11-01T10:00"）→ 拒收该条而非崩溃 → Task 3 `test_validate_daily_rejects_non_iso_dates`。
2. agent 顶层 JSON 结构不对（`new_models` 缺失 / 不是列表 / 条目缺 name）→ 整厂商暂态，不崩 → Task 3 `test_daily_answer_schema_violation_returns_none`。
3. SSE `data:` 行不是 JSON（如 "data: [DONE]"、CDN 注入文本）→ 忽略该行不崩；流最终无 message_end → 暂态 → Task 1 `test_non_json_data_lines_are_ignored`。
4. 2xx 但 content-type 非 `text/event-stream`（CF Access 未带服务令牌头时 302→登录页 HTML）→ 暂态且错误信息含实际 content-type，便于定位是鉴权配置问题 → Task 1 `test_2xx_non_sse_body_is_transient_with_content_type`。
5. 同一答案两个候选 slug 相同（"GLM-5.3" 与 "GLM 5.3"）→ 只入库一次，第二个按已知忽略 → Task 3 `test_duplicate_slug_within_answer_ignored`。

---

### Task 1: Dify 客户端 `core/agent_backend.py`

**Files:**
- Modify: `pyproject.toml`（dependencies 加 `"httpx-sse>=0.4"`）
- Create: `src/free_model_spider/core/agent_backend.py`
- Test: `tests/test_agent_backend.py`

**Interfaces（Produces，后续 Task 依赖）:**
```python
class AgentAnswer(NamedTuple):
    text: str          # agent_message 聚合 / message_replace 覆盖后的最终回答全文
    usage: dict | None # message_end.metadata.usage

class AgentTransientError(RuntimeError): ...
class AgentStructuralError(RuntimeError): ...

class AgentBackend(Protocol):
    def ask(self, query: str) -> AgentAnswer: ...

class DifyAgentBackend:
    def __init__(self, client: httpx.Client, base_url: str, token: str,
                 cf_client_id: str | None = None, cf_client_secret: str | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def ask(self, query: str) -> AgentAnswer: ...

def build_agent_backend(client: httpx.Client) -> DifyAgentBackend:
    # 读环境变量；缺 DIFY_API_BASE/DIFY_API_TOKEN、或 CF 两变量只配一个 → AgentStructuralError
```
常量：`DIFY_USER = "fms-vendors"`、`STREAM_TIMEOUT = httpx.Timeout(15, read=300, write=30, pool=15)`（按位参数顺序 connect/read/write/pool）、`WALL_CLOCK_LIMIT_SECONDS = 900.0`、`RETRY_DELAY_SECONDS = 30.0`。

行为规格（测试即规格）：端点 `base_url.rstrip("/") + "/chat-messages"`；请求头 `Authorization: Bearer <token>`，CF 两值齐备时附 `CF-Access-Client-Id` / `CF-Access-Client-Secret`（`Accept`/`Cache-Control` 由 httpx-sse 自动加）；用 `httpx_sse.connect_sse(client, "POST", url, json=body, headers=..., timeout=STREAM_TIMEOUT)` 打开流。事件分发按 `sse.json()["event"]`：`agent_message`/`message` 追加 `answer`；`message_replace` 整体替换缓冲；`agent_thought` 打印一行 `[dify] thought#{position} tool={tool} input={str(tool_input)[:80]}`；`message_end` 记 usage 并结束；`error` 抛暂态（带 status/code/message）；`ping` 与未知事件、以及 `sse.json()` 解析失败的事件忽略。循环内 `clock()` 距起始超过 900 秒 → 暂态。流耗尽无 `message_end` → 暂态。HTTP 状态：401/403/400/404 → 结构性（信息含响应体前 200 字）；429/≥500/`httpx.TransportError`（含 `httpx_sse.SSEError`）→ 暂态；其中"首字节前"（`connect_sse` 打开或状态检查即失败）重试 1 次（`sleep(30)`），流中途失败不重试。

- [ ] **Step 1: 加依赖**

```bash
uv add "httpx-sse>=0.4" && uv run python -c "import httpx_sse; print(httpx_sse.__version__ if hasattr(httpx_sse,'__version__') else 'ok')"
```
Expected: pyproject 与 uv.lock 更新，import 成功。

- [ ] **Step 2: 写失败测试 `tests/test_agent_backend.py`**

夹具与用例（`respx.mock` 打桩 `POST https://dify.example.com/v1/chat-messages`；SSE 体用本夹具生成）：

```python
def _sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events)

SSE_HEADERS = {"content-type": "text/event-stream"}
BASE = "https://dify.example.com/v1"
ENV = {"DIFY_API_BASE": BASE, "DIFY_API_TOKEN": "app-token",
       "CF_ACCESS_CLIENT_ID": "cid", "CF_ACCESS_CLIENT_SECRET": "csec"}
```

1. `test_ask_aggregates_until_message_end_and_sends_contract_request`：事件流 `agent_message`(answer="Claude"), `agent_message`(answer=" Opus 6"), `ping`, `message_end`(metadata.usage={"total_tokens": 42}) → `AgentAnswer.text == "Claude Opus 6"`、`usage == {"total_tokens": 42}`；并用 `respx` route 捕获请求断言：URL `/v1/chat-messages`、body 五字段（inputs={}, query, response_mode="streaming", user="fms-vendors", auto_generate_name=False）、头 `Authorization: Bearer app-token`、`CF-Access-Client-Id: cid`、`CF-Access-Client-Secret: csec`。
2. `test_ask_without_cf_vars_omits_cf_headers`：构造 `DifyAgentBackend(client, BASE, "t")`（无 CF）→ 请求头里无 `CF-Access-Client-Id`。
3. `test_message_replace_overrides_buffer`：`agent_message`("坏"), `message_replace`(answer="好"), `message_end` → text=="好"。
4. `test_error_event_raises_transient`：`error`(status=400, code="invalid_param", message="x") → `AgentTransientError`。
5. `test_stream_without_message_end_raises_transient`：只有 `agent_message` → 暂态。
6. `test_http_4xx_auth_errors_are_structural`（parametrize 401/403/400/404，`httpx.Response(status, json={"code": "..."})`）→ `AgentStructuralError`。
7. `test_first_attempt_503_then_success`：`route.side_effect = [httpx.Response(503), httpx.Response(200, headers=SSE_HEADERS, content=_sse(message_end))]`，注入 `sleep=rec.append` → 成功且 `rec == [30.0]`、route 调用 2 次。
8. `test_503_twice_raises_transient_no_more_retry`：两次 503 → 暂态、调用 2 次、sleep 一次。
9. `test_midstream_failure_is_transient_without_retry`：`content=_first_event_then_raise()`（生成器产出首个 `agent_message` 事件后 `raise httpx.ReadTimeout("slow")`）→ 暂态、route 调用 1 次。
10. `test_wall_clock_limit_exceeded`：`clock=lambda: [0, 0, 1000].pop(0)` 型递增桩（首事件后 elapsed=1000）→ 暂态（信息含"墙钟"）。
11. `test_non_json_data_lines_are_ignored`：体含 `b"data: [DONE]\n\n"` + 正常两事件 → 正常返回。
12. `test_2xx_non_sse_body_is_transient_with_content_type`：`httpx.Response(200, text="<html>login</html>")` → `AgentTransientError` 且 str 含 `text/html`。
13. `test_build_agent_backend_validations`：env 只给 BASE 不给 TOKEN → 结构性；CF 只配 ID 不配 SECRET → 结构性；全配 → 返回实例；BASE 带尾 `/` 时请求仍打 `/v1/chat-messages`（route 命中）。
14. `test_base_url_trailing_slash`：`DIFYAgentBackend(client, BASE + "/", "t")` → route 命中同一 URL。

- [ ] **Step 3: 跑测试确认失败**：`uv run pytest tests/test_agent_backend.py -q` → FAIL（模块不存在）

- [ ] **Step 4: 实现 `core/agent_backend.py`**

要点：重试循环只包"打开 `connect_sse` + 读 `event_source.response.status_code` + 分流"这一段；进入事件迭代后任何 `httpx.TransportError`/`SSEError` 直接转暂态。`error` 事件与 `message_end` 的判断都在 `for sse in es.iter_sse()` 内。`ask` 返回前打印一行 `[dify] 完成：耗时 {t:.0f}s usage={usage}`。

- [ ] **Step 5: 跑测试通过**：`uv run pytest tests/test_agent_backend.py -q` → PASS

- [ ] **Step 6: 全量 + 提交**

```bash
uv run pytest -q && git add pyproject.toml uv.lock src/free_model_spider/core/agent_backend.py tests/test_agent_backend.py && git commit -m "feat(vendors): Dify Agent SSE 客户端（httpx-sse，暂态/结构性错误分类）"
```

---

### Task 2: v4 状态模型 `core/vendor_state.py`

**Files:**
- Create: `src/free_model_spider/core/vendor_state.py`
- Test: `tests/test_vendor_state.py`

**Interfaces（Produces）:**
```python
class ModelState(BaseModel):
    key: str; name: str; release_date: str; url: str; added_on: str
    retired_on: str | None = None; superseded_by: str | None = None

class LastCheck(BaseModel):
    date: str; status: Literal["ok", "transient"]; detail: str = ""

class VendorState(BaseModel):
    id: str
    models: list[ModelState] = Field(default_factory=list)
    last_check: LastCheck | None = None
    def active_models(self) -> list[ModelState]: ...   # retired_on is None，保持原序

class Baseline(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: Literal[4] = Field(default=4, alias="schema")
    date: str
    vendors: list[VendorState] = Field(default_factory=list)

WINDOW_FALLBACK_DAYS = 180
def baseline_path(data_dir: Path, date: str) -> Path          # data_dir/"vendor-releases"/f"{date}.json"
def find_latest_baseline(data_dir: Path, on_or_before: str) -> Path | None
def save_baseline(b: Baseline, path: Path) -> None            # model_dump(mode="json", by_alias=True)
def load_baseline(path: Path) -> Baseline                     # schema != 4 → ValueError 含 "--bootstrap"
def days_since(release_date: str, today: str) -> int          # 自 vendor_stats.py:99 原样搬来
def window_start(state: VendorState, today: str) -> str       # max(active release_date) 否则 today-180 天
```
`days_since`/`find_latest_baseline` 的实现搬自现有 `vendor_stats.py:59-107`（目录名改 vendor-releases），本 Task 不删旧模块。

- [ ] **Step 1: 失败测试 `tests/test_vendor_state.py`**

用例：
1. `test_roundtrip_writes_schema_alias`：`save_baseline` 后文件 JSON 顶层有 `"schema": 4` 键（不是 `schema_version`）；`load_baseline` 往返等价。
2. `test_load_rejects_wrong_schema_with_hint`：写入 `{"date": "...", "vendors": []}`（无 schema 键，形如 v2 旧文件）→ `ValueError`，str 含 "schema" 与 "--bootstrap"。
3. `test_baseline_path_uses_vendor_releases_dir`：`baseline_path(tmp, "2026-11-02")` == `tmp/"vendor-releases"/"2026-11-02.json"`。
4. `test_find_latest_baseline_*`：三个沿用旧 `tests/test_vendor_stats.py:122-141` 场景（≤ 边界取最大、目录不存在 None、全在之后 None）。
5. `test_days_since_*`：照搬 `tests/test_vendor_stats.py:143`（含 release 晚于 today 抛 ValueError）。
6. `test_active_models_filters_retired`。
7. `test_window_start_uses_max_active_release_date`：active 有 2026-08-14 / 2026-10-01 → "2026-10-01"；无 active（或 models 空）且 today="2026-11-02" → "2026-05-06"。

- [ ] **Step 2: 跑测试失败** → FAIL
- [ ] **Step 3: 实现**（`window_start` 的回退用 `date.fromisoformat(today) - timedelta(days=WINDOW_FALLBACK_DAYS)`）
- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: 全量 + 提交**

```bash
uv run pytest -q && git add src/free_model_spider/core/vendor_state.py tests/test_vendor_state.py && git commit -m "feat(vendors): v4 状态模型（schema 4 基线、active/retired、window_start）"
```

---

### Task 3: 提问/回答契约与状态迁移 `core/vendor_agent.py`

**Files:**
- Create: `src/free_model_spider/core/vendor_agent.py`
- Test: `tests/test_vendor_agent.py`

**Interfaces（Produces; Consumes: Task 2 的 `ModelState/VendorState/window_start`）:**
```python
class NewModelCandidate(BaseModel):   # model_config = ConfigDict(extra="ignore")
    name: str; release_date: str; url: str; supersedes: str | None = None
class DailyAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    new_models: list[NewModelCandidate] = Field(default_factory=list)
class BootstrapAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    models: list[NewModelCandidate] = Field(default_factory=list)

class Accepted(NamedTuple):
    name: str; release_date: str; url: str; supersedes_key: str | None
class Rejected(NamedTuple):
    name: str; reason: str   # 人类可读，如 "证据域名非官方：xxx"

def slugify_name(name: str) -> str                 # casefold → re.sub(r"[^a-z0-9]+","-") → strip("-")
def host_in_whitelist(url: str, official_domains: list[str]) -> bool  # 搬自 vendor_judge.py:103-108
def parse_json_object(text: str) -> dict | None    # 搬自 vendor_judge.py:133-147（剥 ```json 围栏）
def parse_daily_answer(text: str) -> DailyAnswer | None    # 解析/校验失败 → None（整厂商暂态）
def parse_bootstrap_answer(text: str) -> BootstrapAnswer | None
def build_daily_query(vendor_name: str, aliases: list[str], official_domains: list[str],
                      today: str, window_start_day: str, active: list[ModelState]) -> str
def build_bootstrap_query(vendor_name: str, aliases: list[str],
                          official_domains: list[str], today: str) -> str
def validate_daily(answer: DailyAnswer, state: VendorState, official_domains: list[str],
                   window_start_day: str, today: str)
    -> tuple[list[Accepted], list[Rejected], list[str]]   # (接收, 拒收, 忽略的已知模型名)
def validate_bootstrap(answer: BootstrapAnswer, official_domains: list[str], today: str)
    -> tuple[list[Accepted], list[Rejected]]              # 窗口 = [today-3 年, today]
def apply_accepted(state: VendorState, accepted: list[Accepted], today: str) -> list[ModelState]
    # state.models 追加新 ModelState(key=slug, added_on=today)；supersedes_key 命中在表模型
    # → 该模型 retired_on=today、superseded_by=新 key；返回新增的 ModelState 列表
```

校验规则（spec §7.1，逐条）：`release_date` 必须 `date.fromisoformat` 成功且 `window_start_day <= release_date <= today`（否则拒收"日期非法/越界"）；`host_in_whitelist(url)` 不过 → 拒收"证据域名非官方"；`slugify_name(name)` 已在 `state.models` 的 key（本批先接收的也算）→ 忽略并记名；`supersedes` 解析：先匹配在表模型 key，不中再按在表模型 `name.casefold()` 匹配换算 key，仍不中 → `supersedes_key=None`（不拒收）。query 中文模板必须包含：厂商名与别名、官方域名列表、今日日期、窗口起点、逐条 `key（name，发布于 date）` 清单、输出 JSON 结构示例（`{"new_models": [{"name","release_date","url","supersedes"}]}`）、排除规则（只报通用 LLM 新版本；排除已知清单、图像/语音/embedding、仅改名降价；证据 URL 必须官方域名；无新模型回 `{"new_models": []}`）。

- [ ] **Step 1: 失败测试 `tests/test_vendor_agent.py`**

夹具：`_state()` 返回含 `ModelState(key="claude-opus-5-5", name="Claude Opus 5.5", release_date="2026-08-14", url="https://www.anthropic.com/news/a", added_on="2026-08-14")` 的 `VendorState(id="anthropic")`；`DOMAINS=["anthropic.com"]`；today="2026-11-02"。

用例：
1. `test_slugify_name`：`"Claude Opus 5.5"→"claude-opus-5-5"`、`"GLM-5.3-Flash!"→"glm-5-3-flash"`、`"  Qwen3.8 Max "→"qwen3-8-max"`。
2. `test_host_in_whitelist_blocks_lookalike`（照 spec §13）：`"https://openai.com.evil.net/x"` False；`"https://blog.google/..."`（domains=["blog.google"]）True；子域 True；`"not-a-url"` False。
3. `test_parse_daily_answer_fences_and_plain`：围栏 ```json 与裸 JSON 均解析；多余字段被 ignore。
4. `test_daily_answer_schema_violation_returns_none`（Review Focus 2）：`{"models": []}`、`{"new_models": "x"}`、条目缺 `url`、纯文本 → `None`。
5. `test_validate_daily_accepts_and_resolves_supersedes_by_name`：候选 `{"name":"Claude Opus 6","release_date":"2026-11-01","url":"https://www.anthropic.com/news/opus6","supersedes":"Claude Opus 5.5"}` → accepted.supersedes_key=="claude-opus-5-5"。
6. `test_validate_daily_rejects_non_iso_dates`（Review Focus 1）："2026/11/01"、"November 1, 2026"、"2026-11-01T10:00" 全拒收（原因含"日期"）。
7. `test_validate_daily_rejects_out_of_window`：早于 window_start、晚于 today（未来日期）各拒收。
8. `test_validate_daily_rejects_non_official_url` + 拒收项 `Rejected.name` 原样。
9. `test_validate_daily_ignores_known_model`：候选 name slug 等于既有 key → 进 ignored，accepted/rejected 均空。
10. `test_duplicate_slug_within_answer_ignored`（Review Focus 5）："GLM-5.3" 与 "GLM 5.3" 同批 → accepted 1 条、ignored 1 条。
11. `test_supersedes_unknown_becomes_none`：supersedes="gpt-7" → accepted 但 supersedes_key None。
12. `test_apply_accepted_adds_and_retires`：apply 后 state.models 长度 +1；旧模型 `retired_on=="2026-11-02"`、`superseded_by=="claude-opus-6"`；`active_models()` 不再含旧模型。
13. `test_build_daily_query_contains_contract`：query 含厂商名、每个别名、每个域名、"2026-11-02"、窗口日、`claude-opus-5-5（Claude Opus 5.5，发布于 2026-08-14）` 形态的条目、`new_models`、`{"new_models": []}`。
14. `test_build_bootstrap_query_contains_contract`：含 `models`、排除规则关键词（"最新一代"/"embedding"）。
15. `test_validate_bootstrap_window_three_years`：today-3 年整的日期（如 today="2026-11-02" 取 "2023-11-03"）接收、"2023-11-01" 拒收；`supersedes` 忽略。

- [ ] **Step 2: 跑测试失败** → FAIL
- [ ] **Step 3: 实现**（注意 `validate_daily` 处理"本批已接收的 key 也要算已知"，实现时维护一个动态 known 集合）
- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: 全量 + 提交**

```bash
uv run pytest -q && git add src/free_model_spider/core/vendor_agent.py tests/test_vendor_agent.py && git commit -m "feat(vendors): agent 提问/回答契约、校验与状态迁移"
```

---

### Task 4: 切换——config/report/cli 重写 + 删除 v3 管线（单一 commit）

**Files:**
- Rewrite: `src/free_model_spider/core/vendor_config.py`、`src/free_model_spider/core/vendor_report.py`、`src/free_model_spider/cli.py`（run_vendors 及 argparse；`_git_commit`/`main` 的 run 分支不动）
- Create: `data/vendors.yml`
- Delete: `src/free_model_spider/core/vendor_source.py`、`vendor_judge.py`、`vendor_confirm.py`、`vendor_stats.py`、`tests/test_vendor_source.py`、`tests/test_vendor_judge.py`、`tests/test_vendor_confirm.py`、`tests/test_vendor_stats.py`、`data/vendor_releases.yml`、`data/vendor-releases/2026-10-30.json`（后两个用 `git rm`）
- Test Rewrite: `tests/test_vendor_config.py`、`tests/test_vendor_report.py`、`tests/test_cli_vendors.py`

**Interfaces（Consumes: Task 1/2/3 全部；Produces: CLI 行为）:**

`vendor_config.py` v4：
```python
class VendorConfig(BaseModel):   # extra="forbid"
    id: str                      # 校验 ^[a-z0-9-]+$
    name: str
    official_domains: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
class VendorsConfig(BaseModel):  # extra="forbid"；vendors min_length=1；id 去重校验
def load_config(path: Path) -> VendorsConfig   # YAML 错误包装 ValueError（沿用 vendor_config.py:76-83）
```

`vendor_report.py` v4：`ModelRow(vendor_name, key, name, url, release_date, days)` 五字段；`rows_from_baseline(config: VendorsConfig, baseline: Baseline, today: str)`——配置厂商序、组内 active 模型按 `release_date` 倒序、`days=days_since(...)`；表头 `| 厂商 | 模型 | 发布日 | 距今（天） | 证据 |`，模型名不链接、证据列为 `[公告]({url})`（无 url 输出 `—`）；`LEGEND_LINE = "> 发布日与证据均来自厂商官方公告（agent 检索，域名白名单校验）"`；`render_markdown` 页脚 `基线快照：data/vendor-releases/{date}.json · 口径：官方公告日（Asia/Shanghai）`；`render_json` 顶层 `{"schema_version": 4, "date", "models": [{vendor, key, name, url, release_date, days}]}`；`render_pending_markdown(date, failed: list[PendingLine], transient: list[PendingLine], rejected: list[RejectedLine])` 三节（"未建基线 / 处理异常"、"暂态失败（下次运行重试）"、"拒收条目"，`PendingLine(vendor_name, reason)`、`RejectedLine(vendor_name, name, reason, excerpt)`，空节省略，拒收行含 excerpt 前 500 字）；`update_readme_freshness` 逻辑不动（vendor_report.py:192-210）。

`cli.py`：argparse `vendors` 子命令改为 `--date/--data-dir/--reports-dir/--readme/--no-commit` + `--config`（默认 `data/vendors.yml`）+ `--bootstrap`（store_true）+ `--vendor`（str，仅 bootstrap 合法，与 `--bootstrap` 同用时校验存在于 yml）；`run_vendors` 按下述流程实现：

```
daily（spec §8.1）：
  date → load_config → find_latest_baseline（None → print + send_alert("大模型新鲜度日报失败", …) + return 1）
  → build_agent_backend（结构异常同样 print + alert + return 1，不写基线）
  → baseline.date = date；per vendor in config（yml 有基线无 → failed.append(f"{id} 未建基线…")，continue）：
      try: query=build_daily_query(...) → ans=backend.ask(query)
           parse_daily_answer(None → run_failures.append(transient) + last_check=transient，continue)
           accepted, rejected, ignored = validate_daily(...)
           apply_accepted(state, accepted, date)；last_check=ok/ok+新增N
           rejected → pending 收集；accepted 非空 → 新模型告警行
      except AgentTransientError → run_failures + last_check=transient
      except AgentStructuralError → 顶层处理：alert + return 1（不写基线、不 commit）
      except Exception → failed.append(id)
      每厂商收尾 print："[vendors] {id}: 接收 x / 拒收 y / 忽略 z（窗口 {起点}）"（spec §10 日志）
  （yml 中已移除的厂商不进循环：状态块随 baseline 复制保留、不提问不渲染）
  rows → 写 md/json/pending(如有) → update_readme → save_baseline → _git_commit(message="docs: 大模型新鲜度日报 {date} [skip ci]")
  → 有 accepted → send_alert("发现新模型", 逐行 "- {vendor} / {name} / {date} / {url}（取代 {旧name}）" + run_link())
  → run_failures 或 rejected 或 failed → send_alert("大模型新鲜度厂商异常", 合并明细 + run_link())
  → return 1 if failed else 0
bootstrap（spec §8.2）：
  未指定 --vendor 且已存在任意基线 → print 提示 + return 1
  指定 --vendor：latest 存在则复制并只替换该厂商块，否则全新 Baseline
  逐目标厂商 build_bootstrap_query → ask → parse_bootstrap_answer → validate_bootstrap
  （空清单厂商写入空 models 块并醒目 print；暂态厂商同样写入块并标 last_check=transient）
  → 写 data/vendor-releases/{date}.json（schema 4）；print 逐厂商表格；不写 reports/README、不 commit；return 0（有暂态 → return 1）
```

`data/vendors.yml`（8 家，域名迁自 v3 data/vendor_releases.yml:13-139）：

```yaml
# 厂商身份；模型清单由 data/vendor-releases/ 状态文件承载，不在此维护（v4 spec 2026-11-02）
vendors:
  - {id: openai, name: OpenAI, official_domains: [openai.com]}
  - {id: anthropic, name: Anthropic, official_domains: [anthropic.com], aliases: [Claude]}
  - {id: google, name: Google DeepMind, official_domains: [blog.google, deepmind.google, ai.google.dev], aliases: [Gemini]}
  - {id: deepseek, name: DeepSeek, official_domains: [deepseek.com, api-docs.deepseek.com]}
  - {id: qwen, name: 阿里 Qwen, official_domains: [qwen.ai, qwenlm.github.io], aliases: [Qwen, 通义千问]}
  - {id: moonshot, name: 月之暗面, official_domains: [moonshot.cn, moonshot.ai, kimi.com], aliases: [Moonshot AI, Kimi]}
  - {id: zhipu, name: 智谱 AI, official_domains: [z.ai, bigmodel.cn, zhipuai.cn], aliases: [Zhipu AI, GLM]}
  - {id: minimax, name: MiniMax, official_domains: [minimax.io, minimaxi.com]}
```

- [ ] **Step 1: 重写三份测试文件（先写，全部针对新接口）**

`tests/test_vendor_config.py`：`test_loads_real_vendors_yml`（加载仓库 `data/vendors.yml`，8 家、id 集合断言）、`test_legacy_fields_rejected`（带 `models:` 的 v3 片段 → ValidationError）、`test_duplicate_id_rejected`、`test_invalid_id_rejected`（大写）、`test_yaml_error_wrapped`（沿用旧 109 行场景）。

`tests/test_vendor_report.py`（夹具自建 v4 Baseline）：`test_rows_active_only_and_sorted_desc`（两个 active + 一个 retired，断言 retired 不出现、组内倒序）、`test_render_markdown_contains_five_columns_and_legend`、`test_render_json_schema4`、`test_render_pending_three_sections_and_omits_empty`（给 failed+rejected，不给 transient → transient 节不出现；excerpt 超 500 字截断）、`test_update_readme_freshness_rewrite_idempotent`（沿用旧 211 行场景改新行模型）。

`tests/test_cli_vendors.py` 重写（模式沿用现有 `_init_repo/_clean_env/_capture_alerts`，tests/test_cli_vendors.py:113-158；新增）：

```python
class FakeBackend:
    def __init__(self, answers: dict[str, str | Exception]):  # key = 厂商显示名子串
        self.queries: list[str] = []
        ...
    def ask(self, query): 记录 query；匹配厂商返回 AgentAnswer(text, None) 或 raise
def _stub_backend(monkeypatch, fake):  # monkeypatch free_model_spider.cli.build_agent_backend
VENDORS_YML = ...  # anthropic + moonshot 两家的小配置
def _baseline() -> Baseline:  # anthropic 一个 active 模型 claude-opus-5-5（2026-08-14）
```

集成用例（spec §13）：
1. `test_new_model_added_old_retired_alert_and_commit`：answer 含 Opus 6（supersedes=key）→ exit 0；README/report 含 "Claude Opus 6" 不含 "Claude Opus 5.5"；当日 JSON 里旧模型 `retired_on` 非空；alerts 含 `("发现新模型", …)` 且 content 有 "claude-opus-5-5" 或 "Claude Opus 5.5"；git log 最新 `docs: 大模型新鲜度日报 {date} [skip ci]`；tracked 含 `data/vendor-releases/{date}.json`。
2. `test_transient_error_keeps_state_alerts_exit_zero`：anthropic 抛 `AgentTransientError("超时")` → exit 0；该厂商模型原样；`-pending.md` 存在且含"下次运行重试"；alerts 含 `大模型新鲜度厂商异常`。
3. `test_rejected_candidate_goes_to_pending_not_state`：候选 url=`https://evil.example.com/x` → 不入库；pending 含"证据域名非官方"与 agent 原文。
4. `test_vendor_in_yml_missing_from_baseline_fails_without_asking`：yml 两家、基线只 anthropic → exit 1；pending 含"未建基线"；FakeBackend.queries 不含 moonshot。
5. `test_structural_error_aborts_no_baseline_exit_one`：backend 抛 `AgentStructuralError("HTTP 401")` → exit 1；`data/vendor-releases/{date}.json` 不存在；alerts 含 `大模型新鲜度日报失败`。
6. `test_no_baseline_at_all_is_structural`：空 data 目录 → exit 1、alerts 含 `大模型新鲜度日报失败`、content 含 "--bootstrap"。
7. `test_bootstrap_writes_draft_without_commit`（无既有基线）：answers 两家 → `data/vendor-releases/{date}.json` 存在且 schema 4；README 与 reports/ 无新文件；`git log` 行数不变；exit 0。
8. `test_bootstrap_refuses_when_baseline_exists`：已有基线 + 无 `--vendor` → exit 1，原基线文件未被覆盖（mtime/内容断言）。
9. `test_bootstrap_vendor_merges_only_that_vendor`：基线含 anthropic；`--vendor moonshot` → moonshot 块来自草稿，anthropic 块 `model_dump(by_alias=True)` 与原基线完全相等。
10. `test_bootstrap_unknown_vendor_id_rejected`：`--vendor nope` → exit 1、不提问。
11. `test_bootstrap_transient_marks_and_returns_one`：一家暂态 → 草稿仍写出（该厂商块留空 + last_check transient）→ exit 1。
12. `test_removed_vendor_block_carried_forward`：基线含 yml 已移除的厂商 `oldco`（带一个模型）→ 不出现在 FakeBackend.queries、不出现在报告，但当日 JSON 的 vendors 里原样保留。

- [ ] **Step 2: 跑新测试失败** → FAIL（ImportError/旧签名不符均算）

- [ ] **Step 3: 按上述接口重写 config → report → cli；`git rm data/vendor_releases.yml data/vendor-releases/2026-10-30.json`；删除四个旧 src 模块与三份旧 tests；写入 `data/vendors.yml`**

cli 细节：导入改为 `from free_model_spider.core.agent_backend import build_agent_backend`、`from free_model_spider.core.vendor_state import ...`、`from free_model_spider.core.vendor_agent import ...`；删除 `_corroboration_map`（cli.py:80-100）与 run_vendors 内目录/LLM 逻辑（cli.py:137-204 对应段）；`fms run` 分支（cli.py:278-353）与 `_git_commit` 不动。

- [ ] **Step 4: 全量通过**：`uv run pytest -q` → 全 PASS（test_cli.py 的 `fms run` 测试不受影响）

- [ ] **Step 5: 提交（单一切换 commit）**

```bash
git add src/free_model_spider/core/vendor_config.py src/free_model_spider/core/vendor_report.py src/free_model_spider/cli.py data/vendors.yml tests/test_vendor_config.py tests/test_vendor_report.py tests/test_cli_vendors.py && git rm -q src/free_model_spider/core/vendor_source.py src/free_model_spider/core/vendor_judge.py src/free_model_spider/core/vendor_confirm.py src/free_model_spider/core/vendor_stats.py tests/test_vendor_source.py tests/test_vendor_judge.py tests/test_vendor_confirm.py tests/test_vendor_stats.py data/vendor_releases.yml data/vendor-releases/2026-10-30.json && git commit -m "feat(vendors)!: v4 切换——Dify Agent 监测替代提取管线，模型清单移出配置"
```

---

### Task 5: workflow、.env.example 与收尾验证

**Files:**
- Modify: `.github/workflows/vendors.yml`、`.env.example`

**Interfaces:** 无代码接口；产物为 CI 配置。

- [ ] **Step 1: 改 `.github/workflows/vendors.yml`**

`on:` 增加 `workflow_dispatch: inputs: mode: {description: run=日常监测, bootstrap=生成基线草稿, type: choice, options: [run, bootstrap], default: run} vendor: {description: 仅 bootstrap 用的厂商 id, required: false, type: string}`。步骤改为：

```yaml
      - name: Run vendor freshness report
        env:
          DIFY_API_BASE: ${{ vars.DIFY_API_BASE }}
          DIFY_API_TOKEN: ${{ secrets.DIFY_API_TOKEN }}
          CF_ACCESS_CLIENT_ID: ${{ secrets.CF_ACCESS_CLIENT_ID }}
          CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
          ALERT_WEBHOOK_URL: ${{ secrets.ALERT_WEBHOOK_URL }}
          ALERT_WEBHOOK_TOKEN: ${{ secrets.ALERT_WEBHOOK_TOKEN }}
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          if [ "${{ inputs.mode }}" = "bootstrap" ]; then
            uv run fms vendors --bootstrap --no-commit ${{ inputs.vendor && format('--vendor {0}', inputs.vendor) || '' }}
          else
            uv run fms vendors
          fi

      - name: Upload baseline draft
        if: ${{ inputs.mode == 'bootstrap' }}
        uses: actions/upload-artifact@v6
        with: {name: vendor-baseline-draft, path: data/vendor-releases/}
```
（LLM_* 四行整体移除；cron/concurrency/ref/name 已在 64eb390 定稿，不动。）

- [ ] **Step 2: 验证 yml 可解析且关键点在位**

```bash
uv run python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/vendors.yml', encoding='utf-8'))
on = d.get(True, d.get('on'))
assert any('*/2' in s['cron'] for s in on['schedule'])
assert {'mode', 'vendor'} <= set(on['workflow_dispatch']['inputs'])
text = open('.github/workflows/vendors.yml', encoding='utf-8').read()
assert 'DIFY_API_TOKEN' in text and 'LLM_API_KEY' not in text and 'upload-artifact' in text
print('workflow ok')"
```

- [ ] **Step 3: `.env.example` 追加（LLM/BRAVE 行保留，`fms run` 仍用）**

```bash
# 厂商新鲜度（fms vendors）：Dify Agent
DIFY_API_BASE=https://dify.example.com/v1
DIFY_API_TOKEN=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

- [ ] **Step 4: 全量测试 + 提交**

```bash
uv run pytest -q && git add .github/workflows/vendors.yml .env.example && git commit -m "ci(vendors): workflow 接入 Dify/CF 变量与 bootstrap 手动模式"
```

---

## Spec 覆盖对照（自查结果）

§4 配置→Task 4；§5 状态→Task 2/3；§6 客户端→Task 1；§7 契约→Task 3；§8.1 daily→Task 4；§8.2 bootstrap→Task 4；§8.3 切换顺序→Task 4 Step 5 的删除 + Task 5（workflow 在同一推送批次内）；§9 渲染→Task 4；§10 告警→Task 4（标题字面量入 Global Constraints）；§11 workflow/依赖→Task 1/5；§12 删除清单→Task 4；§13 测试→各 Task 用例 + Review Focus；§14 留白→query 措辞（Task 3 已给必含要素）、模块名（已定）、表格格式（Task 4 bootstrap stdout 自由）；§15 风险→对应测试已列。
