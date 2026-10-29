# 厂商大模型新鲜度（vendor-release-stats）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日自动统计 15 家主流厂商旗舰/次旗舰大模型距官方公告日的天数，生成 `reports/各大厂商大模型新鲜度-YYYY-MM-DD.{md,json}` 与 README 常驻表，故障经 webhook 通知。

**Architecture:** 目录（OpenRouter 全量 API + models.dev）做免费的"新条目/stale"事件检测；仅被触发的厂商走 Brave 官方域搜索 + LLM 提取（防捏造 prompt + 程序侧 evidence 校验 + 域名白名单 + 二次原文确认）；结果写入逐日基线快照，报告由基线渲染。并入现有 daily workflow，复用现有 httpx/pydantic/重试/LLM/搜索/提交基建。

**Tech Stack:** Python 3.14 + uv、httpx、pydantic、新增 `pyyaml>=6.0`；测试 pytest + respx（LLM/搜索用 stub 注入，不打真实网络）。

**Spec:** `docs/superpowers/specs/2026-10-29-vendor-release-stats-design.md`（本计划从 spec 出发，执行者须同时阅读 spec）

## Global Constraints

- Python `>=3.14`；新依赖仅 `pyyaml>=6.0`，其余复用现有（httpx/pydantic/pytest/respx）。
- 所有外部 HTTP 一律走 `free_model_spider.net.request_with_retries`（src/free_model_spider/net.py:7）。
- 文件名 verbatim：报告 `reports/各大厂商大模型新鲜度-YYYY-MM-DD.md` + 同名 `.json`；基线 `data/vendor-releases/YYYY-MM-DD.json`；配置 `data/vendor_releases.yml`。
- 提交信息：本功能各任务用 `feat(vendors): ...` / `test(vendors): ...`；日报运行时 commit 为 `docs: 大模型新鲜度日报 YYYY-MM-DD [skip ci]`。
- 密钥：新增 `ALERT_WEBHOOK_URL`（GitHub Secret / `.env`），复用 `LLM_API_KEY`/`BRAVE_API_KEY`/`LLM_API_BASE`/`LLM_MODEL`/`LLM_FALLBACK_MODELS`；密钥永不进仓库、不进日志。
- 测试不依赖真实网络与真实 key；CI 在无 key 环境全绿。
- 时区：日期一律 `Asia/Shanghai`（复用 cli.py 的 `SHANGHAI`）。
- 报告极简（spec §8）：标题 + 一张长表 + ⧉ 图例行 + 页脚行，无其他小节；JSON 仅 `schema_version/date/models[]`，每条 `{vendor, name, url, release_date}`。

## Review Focus

spec 未逐条点名、但最可能坑到真实使用者的五类输入，及其钉死测试所在任务：

1. **LLM 编造日期**（返回的日期在材料原文中不存在）→ `validate_judgment` 必须拒收 → 线保持 pending（Task 4）。
2. **LLM 给非官方域 URL**（新闻站/博客转载链接）→ 域名白名单校验拒收，模型不进正式表（Task 4）。
3. **日期回退与同名抖动**（LLM 隔天返回更早日期 / "GLM-5.4" vs "GLM-5.4-Preview"）→ `apply_judgment` 拒绝回退、同日改名仅修基线名（Task 5）。
4. **首日无基线且无 LLM key** → 空基线全 pending，产出空表报告不崩、退出码 0（Task 8）。
5. **webhook 不可达** → 告警失败只记日志，不影响主流程结果与退出码（Task 7）。

---

### Task 1: 配置模型与 15 家厂商 yml

**Files:**
- Create: `src/free_model_spider/core/vendor_config.py`
- Create: `data/vendor_releases.yml`
- Modify: `pyproject.toml`（dependencies 加 `"pyyaml>=6.0"`，然后 `uv sync`）
- Test: `tests/test_vendor_config.py`

**Interfaces:**
- Produces:
  - `load_config(path: Path) -> VendorReleasesConfig`
  - `VendorReleasesConfig.stale_days: int`（默认 45）、`.vendors: list[VendorConfig]`
  - `VendorConfig.id/name: str`、`.official_domains: list[str]`、`.directory_slugs: dict[str, list[str]]`、`.lines: list[LineConfig]`
  - `LineConfig.key: str`（`'flagship'|'sub'`）、`.name: str`、`.match: list[str]`、`.exclude: list[str] = []`、`.override: Override | None = None`
  - `Override.model/release_date/source: str`
  - pydantic 校验失败抛 `pydantic.ValidationError`（lines 非空且 key 唯一、首个 line 必须 flagship、official_domains/directory_slugs 非空）

- [ ] **Step 1: 写失败测试** `tests/test_vendor_config.py`

用内联 yaml 字符串（`temp_path` 落盘）测：`load_config` 正常返回 15 家；stale_days 默认 45；`data/vendor_releases.yml` 真文件加载成功且 15 家 id 齐全（`openai,anthropic,google,meta,xai,mistral,cohere,deepseek,qwen,moonshot,zhipu,minimax,bytedance,tencent,stepfun`）；flagship 缺失时抛 ValidationError；yaml 语法错抛清晰异常（包装为 `ValueError`，带文件路径）。

- [ ] **Step 2: 跑测试确认失败**（ImportError / 文件不存在）

Run: `uv run pytest tests/test_vendor_config.py -v`

- [ ] **Step 3: 实现 vendor_config.py + 编写 data/vendor_releases.yml**

pydantic BaseModel + `load_config`；yml 内容按 spec §5.1 的 15 家（海外 7 在前、国内 8 在后，顺序即报告厂商顺序）。三个代表厂商的完整示例（其余 12 家照 spec §5.1 表格同构填写，exclude 至少排除 image/video/audio/embed/guard/translate 类衍生词）：

```yaml
stale_days: 45
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai, "~openai"], modelsdev: [openai] }
    lines:
      - key: flagship
        name: GPT 主线
        match: ['gpt-\d']
        exclude: [mini, nano, image, video, audio, embed, transcribe, safeguard, guard]
      - key: sub
        name: GPT mini
        match: ['gpt-\d[\d.]*-?mini']
  - id: google
    name: Google DeepMind
    official_domains: [blog.google, deepmind.google, ai.google.dev]
    directory_slugs: { openrouter: [google], modelsdev: [google] }
    lines:
      - key: flagship
        name: Gemini 主线
        match: ['gemini-\d']
        exclude: [flash, image, video, audio, embedding, guard, nano, live]
      - key: sub
        name: Gemini Flash
        match: ['gemini-\d[\d.]*-?flash']
  - id: zhipu
    name: 智谱 AI
    official_domains: [z.ai, bigmodel.cn, zhipuai.cn]
    directory_slugs: { openrouter: [z-ai], modelsdev: [zai, zhipuai] }
    lines:
      - key: flagship
        name: GLM 主线
        match: ['glm-\d']
        exclude: [flash, air, embedding, voice, vision, guard]
      - key: sub
        name: GLM Flash
        match: ['glm-\d[\d.]*-?(flash|flashx)']
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_vendor_config.py -v` → PASS

- [ ] **Step 5: 核对 slug 覆盖（一次性人工核对，非单测）**

Run: `curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; print(sorted({m['id'].split('/')[0] for m in json.load(sys.stdin)['data']}))"` 与 `curl -s https://models.dev/api.json | python3 -c "import json,sys; print(list(json.load(sys.stdin)))"`，逐一确认 yml 里每个 slug 确实出现在对应目录；缺失的改用实际 slug。

- [ ] **Step 6: Commit**

```bash
git add src/free_model_spider/core/vendor_config.py data/vendor_releases.yml tests/test_vendor_config.py pyproject.toml uv.lock
git commit -m "feat(vendors): 厂商配置模型与 15 家清单"
```

### Task 2: 基线快照模型与天数计算

**Files:**
- Create: `src/free_model_spider/core/vendor_stats.py`
- Test: `tests/test_vendor_stats.py`

**Interfaces:**
- Consumes: Task 1 的 `VendorReleasesConfig/VendorConfig/LineConfig`
- Produces:
  - `LineState(key: str, model: str|None=None, release_date: str|None=None, url: str|None=None, last_verified: str|None=None, status: Literal['ok','pending','stale-baseline']='pending', reason: str='', pending_days: int=0)`（pydantic）
  - `VendorState(id: str, seen_directory_ids: list[str]=[], lines: list[LineState]=[])`；`Baseline(date: str, vendors: list[VendorState])`
  - `baseline_path(data_dir: Path, date: str) -> Path`（`data_dir/'vendor-releases'/f'{date}.json'`）
  - `find_latest_baseline(data_dir: Path, on_or_before: str) -> Path | None`（`stem <= on_or_before`，支持同日幂等重跑）
  - `load_baseline(path: Path) -> Baseline` / `save_baseline(b: Baseline, path: Path) -> None`（json、ensure_ascii=False、indent=2、结尾换行，对齐 snapshot.py 风格）
  - `empty_baseline(date: str, config: VendorReleasesConfig) -> Baseline`（每厂商每线 pending、seen 空）
  - `days_since(release_date: str, today: str) -> int`（纯日期减法，`datetime.date.fromisoformat`）

- [ ] **Step 1: 写失败测试**

覆盖：roundtrip 保存/加载保结构；`find_latest_baseline` 选 `<=` 最大日期且忽略无 json 目录；`days_since('2026-10-26','2026-10-29')==3`、跨月 `'2026-09-30','2026-10-02')==2`；`empty_baseline` 每厂商线数=配置线数、status 全 pending。

- [ ] **Step 2: 跑测试确认失败** Run: `uv run pytest tests/test_vendor_stats.py -v`
- [ ] **Step 3: 实现**（纯函数 + pydantic，无 IO 之外的逻辑）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git add ... && git commit -m "feat(vendors): 基线快照模型与天数计算"`

### Task 3: 目录采集与事件检测（确定性层）

**Files:**
- Modify: `src/free_model_spider/core/vendor_stats.py`（追加）
- Test: `tests/test_vendor_stats.py`（追加）

**Interfaces:**
- Consumes: Task 1/2 全部；`request_with_retries`
- Produces:
  - `collect_directory_entries(client: httpx.Client, config: VendorReleasesConfig) -> tuple[dict[str, list[str]], list[str]]`：OpenRouter `https://openrouter.ai/api/v1/models`（全量，取 `data[].id`，`id.split('/')[0]` 为 slug）+ models.dev `https://models.dev/api.json`（各 provider 节点 `models` 键下条目 id，条目归一为 `f"{provider}:{model_id}"`）。返回 `vendor_id -> 条目列表`（两家合并去重）与错误列表（单家失败降级：跳过该家记 error；两家全失败返回 `({}, [两条error])`）。**HTTP 全部 respx mock，不在单测打真实网络**。
  - `screen_new_entries(vendor: VendorConfig, state: VendorState, entries: list[str]) -> list[str]`：对条目 id 小写做 `match`（任一 regex `re.search` 命中）且不被 `exclude`（任一子串命中）粗筛，再剔除 `seen_directory_ids` 中已有；override 线存在时该线不参与（Task 5 用不到此细节，此处按 vendor 级返回即可）。
  - `detect_review(config, baseline: Baseline, entries_by_vendor: dict[str, list[str]], today: str) -> dict[str, list[str]]`：返回 `vendor_id -> 触发原因列表`（元素取 `'new-entry'` / `'stale'`）。规则：有新条目 → `new-entry`；无 override 的厂商其全部线满足 `today - max(release_date 或 last_verified 或 '1970-01-01') > stale_days` → `stale`；`entries_by_vendor` 缺该厂商（目录全失败）时仅按 stale 判定。

- [ ] **Step 1: 写失败测试**（respx mock 两目录）

用例：新条目触发（seen 有旧条目、目录多出一个命中 match 的）；exclude 命中不触发（`gemini-4-image` 被 flash 之外的 exclude 挡住）；models.dev 挂靠条目（`mistral:deepseek-v4-1-flash` 在 mistral 厂商下、match 不命中 mistral 的 lines → 不触发）；单目录 500 → 降级不抛、errors 记录；双目录失败 → 空 dict；stale 触发（`last_verified` 重臂：`today-max(release,last_verified)>45` 才触发，复核刷新 last_verified 后不再触发）；首日空基线 → 全部厂商 `stale` 触发（`'1970-01-01'` 兜底）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（注意 OpenRouter 全量 457 模型字段：`resp.json()['data']` 每项 `id`；models.dev：顶层 `provider -> {models: {id: {...}}}`）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): 目录采集与事件检测"`

### Task 4: LLM 裁决管线（prompt 基座 + evidence 校验）

**Files:**
- Create: `src/free_model_spider/core/vendor_judge.py`
- Test: `tests/test_vendor_judge.py`

**Interfaces:**
- Consumes: `LLMClient.complete(system, user)`（llm/client.py:35）、`SearchProvider.search(query, count)`、`request_with_retries`、Task 1 的 `VendorConfig/LineConfig`
- Produces:
  - `SYSTEM_PROMPT: str`（spec §7.1 五条硬约束的中文 system 基座，逐条落实：只依据材料/禁用记忆、禁推算估算、非空字段必须附 evidence 且 URL 限官方域、不确定输出 null+reason、只输出一个 JSON 对象）
  - `Judgment(model: str|None, release_date: str|None, source_urls: list[str]=[], evidence: list[str]=[], reason: str='')`（pydantic）
  - `validate_judgment(j: Judgment, official_domains: list[str]) -> bool`：`model/release_date` 任一非 None 时——两者须均非 None、`evidence` 非空、`release_date`（`YYYY-MM-DD` 归一后）作为字符串出现在任一 evidence 或 source_urls 中任一 URL 所属域在白名单内、`source_urls` 非空且全部 URL 的 host 以白名单域结尾（`urlsplit(u).hostname` endswith 域，忽略大小写）。任一不满足 → False。
  - `classify_entry(llm, vendor: VendorConfig, entry_id: str) -> tuple[str, str]`：返回 `('flagship'|'sub'|'ignore', reason)`；LLM 不可用/解析失败/重试 1 次仍坏 → `('pending', '')`
  - `extract_line(llm, search, vendor, line: LineConfig, search_sleep) -> tuple[Judgment|None, list[SearchResult]]`：构造 query（见 Step 3）→ `search.search(query, count=8)` → LLM 提取 → `_parse_llm_json`（复用 intro.py 的代码模式，提取 `{...}` 子串）→ `Judgment` 构造 + `validate_judgment`（不过 → `(None, results)`）；search 为 None/异常 → `(None, [])`；LLM 失败重试 1 次（沿用 intro.py 两次循环模式）
  - `confirm_from_page(llm, client, vendor, judgment: Judgment) -> Judgment|None`：GET `source_urls[0]`（`request_with_retries`、follow_redirects），正文截 8000 字符，LLM 从原文确认 model/release_date（同 prompt 基座），再 validate；失败/不过 → None
- 查询模板（测试断言用，逐字）：`query = " OR ".join(f"site:{d}" for d in vendor.official_domains[:3]) + f" {vendor.name} {line.name} 发布"`

- [ ] **Step 1: 写失败测试**（LLM/搜索均为 stub 对象，不 respx LLM）

用例：`validate_judgment` 五连——①无 evidence 拒 ②日期不在 evidence 文本拒 ③`source_urls` 含非白名单域（`techcrunch.com`）拒 ④全 None+reason 放行 ⑤官方域 URL + evidence 含日期通过；`extract_line` 的 query 逐字断言；LLM 返回 ```json 围栏代码块可解析；LLM 两次坏 JSON → `(None, ...)`；`classify_entry` 正常分类与失败降级 `pending`；`confirm_from_page` 用 respx mock 官方页 200（原文含日期 → 通过返回；原文无日期 → None）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（prompt 具体措辞由实现者按 SYSTEM_PROMPT 约束写，测试只钉行为不钉文案）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): LLM 裁决管线与 evidence 校验"`

### Task 5: 裁决编排、防倒退与 pending 状态机

**Files:**
- Modify: `src/free_model_spider/core/vendor_stats.py`（追加）
- Test: `tests/test_vendor_stats.py`（追加）

**Interfaces:**
- Consumes: Task 3 `detect_review`、Task 4 全部
- Produces:
  - `apply_judgment(state: LineState, judgment: Judgment|None, today: str) -> LineState`（纯函数，返回新对象）：
    - `judgment=None`（提取失败/校验不过/确认失败）→ 原 model/date/url 保留，`status='pending'`，`pending_days+=1`，reason 记来源环节
    - judgment 有效且 `release_date > state.release_date`（日期前进）→ 更新 model/url/release_date，`status='ok'`，`pending_days=0`，`last_verified=today`
    - judgment 有效且 `release_date == state.release_date` 且 model 字符串不同 → 仅更新 model/url（命名修正），`status='ok'`，`pending_days=0`，`last_verified=today`
    - judgment 有效但 `release_date < state.release_date` → 拒绝（保持原值），`status='pending'`，reason='防倒退拒绝'
    - state.release_date 为 None（首日确认）→ 有效 judgment 直接写入，status='ok'
  - `finalize_state(state: LineState, today: str) -> LineState`：复核完成无新发布 → `last_verified=today`、ok 线保持 ok；`pending_days>=7` → `status='stale-baseline'`（冻结）
  - `update_seen(vendor: VendorConfig, state: VendorState, entries: list[str], labels: dict[str, tuple[str,str]]) -> list[str]`：新 seen = 旧 seen + 粗筛候选中"有终态结论"的条目（label 为 ignore，或 label 为线且该线当日裁决结束 status != pending）

- [ ] **Step 1: 写失败测试**

覆盖 Review Focus 3 全部分支（前进/同日改名/回退拒绝/首日写入/None→pending 计数）；`finalize_state` 重臂与 7 天冻结；`update_seen`：pending 线的条目不入 seen（次日重触发）、ignore 条目入 seen、冻结后入 seen。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（纯函数，日期比较用 ISO 字典序）
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): 裁决编排、防倒退与 pending 状态机"`

### Task 6: 报告渲染（md 长表 + 同名 JSON + README 区块）

**Files:**
- Create: `src/free_model_spider/core/vendor_report.py`
- Test: `tests/test_vendor_report.py`

**Interfaces:**
- Consumes: Task 2 `Baseline/LineState/days_since`、Task 1 config（厂商顺序/显示名/线顺序）
- Produces:
  - `VendorRow(NamedTuple)`: `vendor_name: str, model: str, url: str, corroborating: str | None, release_date: str, days: int`
  - `rows_from_baseline(config, baseline: Baseline, today: str, corroborations: dict[str, str]) -> list[VendorRow]`：按配置厂商顺序、组内 lines 顺序（flagship 在前）；只收 `model 且 url 且 release_date` 均非空的线（pending/冻结且从未确认的线不出现）；`corroborations` 为 `model 目录条目id -> 平台页 URL`（cli 侧由 OpenRouter 条目构造 `https://openrouter.ai/{id}`）
  - `render_markdown(date: str, rows: list[VendorRow]) -> str`：`# 各大厂商大模型新鲜度 {date}`、空行、`## 🥬 各大厂商大模型新鲜度`、四列表 `| 厂商 | 模型 | 发布日 | 天数 |`（组首行显示厂商名、续行空；模型列 `[name](url)` + 有佐证时追加 ` [⧉](corroborating)`）、空行、图例行 `> 模型名链接 = 官方公告/产品页（进表硬条件，域名经白名单校验）；\`⧉\` = 第三方目录佐证（OpenRouter / models.dev 平台页）`、`---`、页脚 `基线快照：data/vendor-releases/{date}.json · 口径：官方公告日（Asia/Shanghai）`、结尾空行
  - `render_json(date: str, rows: list[VendorRow]) -> str`：`{"schema_version": 1, "date": ..., "models": [{"vendor","name","url","release_date"}]}`（ensure_ascii=False、indent=2、结尾换行；rows 为空时 models 为空数组）
  - `FMS_VENDORS_START = "<!-- fms-vendors:start -->"` / `FMS_VENDORS_END = "<!-- fms-vendors:end -->"`
  - `update_readme_freshness(readme: Path, date: str, rows: list[VendorRow]) -> None`：重写两标记间区块为 `### 厂商新鲜度榜` + 同款表 + `> 更新于 {date}`；标记不存在则追加到文末（含标记），风格对齐 report.py:112 的 `update_readme_index`

- [ ] **Step 1: 写失败测试**

golden 断言（精确字符串）：3 厂商 5 行的完整 md；同厂次行厂商名为空串；pending 线被 `rows_from_baseline` 过滤；JSON 结构与字段名 verbatim（无多余字段）；README 区块首写/重写幂等（第二次替换不残留旧行）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): 新鲜度报告渲染与 README 区块"`

### Task 7: webhook 故障告警模块

**Files:**
- Create: `src/free_model_spider/core/alert.py`
- Test: `tests/test_alert.py`

**Interfaces:**
- Produces:
  - `send_alert(client: httpx.Client, title: str, content: str, sleep=time.sleep) -> bool`：无 `ALERT_WEBHOOK_URL` env → 打印跳过、return False 不报错；否则 POST json `{"title": title, "content": content}`（经 `request_with_retries` retries=3）成功 return True；任何异常 catch 后 print 日志 return False（fire-and-forget，不抛）
  - `run_link() -> str`：`GITHUB_ACTIONS=='true'` 时由 `GITHUB_SERVER_URL/GITHUB_REPOSITORY/GITHUB_RUN_ID` 拼 `{server}/{repo}/actions/runs/{run_id}`，否则空串

- [ ] **Step 1: 写失败测试**（respx）

用例：env 未设 → False 且无请求；POST body 逐字段断言 `{"title","content"}`；500→500→200 重试成功 True；连续 500 → False 且不抛；`run_link` 本地空串、Actions env 下拼接正确（monkeypatch env）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): webhook 故障告警模块"`

### Task 8: CLI 子命令 `fms vendors` 与 run 接入告警

**Files:**
- Modify: `src/free_model_spider/cli.py`
- Test: `tests/test_cli_vendors.py`（新增）、`tests/test_cli.py`（追加 run 告警用例）

**Interfaces:**
- Consumes: Task 1-7 全部；现有 `_git_commit` 模式、`build_search_provider`、`build_llm_client`
- Produces:
  - `fms vendors [--date YYYY-MM-DD] [--data-dir data] [--reports-dir reports] [--readme README.md] [--config data/vendor_releases.yml] [--no-commit]`，`run_vendors(args) -> int`
  - 编排（单函数顺序流，厂商循环内 try/except 单厂商隔离，模式对齐 cli.py:108）：load config → `find_latest_baseline(<=date)`（无则 `empty_baseline`）→ `collect_directory_entries` → `detect_review` → 逐待复核厂商逐线：override 线直接采用（跳过判定）→ 新条目先 `classify_entry`（ignore 记入 baseline 审计字段 `state.reason`，不入表）→ `extract_line` →（与基线不一致时）`confirm_from_page` → `apply_judgment`；未触发厂商线 `finalize_state`（重臂/冻结）→ `rows_from_baseline`（佐证 URL 由目录条目 map 构造）→ 写报告 md+json、`update_readme_freshness` → `save_baseline(当日)` → `_git_commit` 变体（add data/vendor-releases reports README，message `docs: 大模型新鲜度日报 {date} [skip ci]`）
  - 告警接入：vendors 任何未捕获异常/写盘失败 → `send_alert("大模型新鲜度日报失败", ...+run_link())` 后非零退出；厂商冻结（`finalize_state` 产生 stale-baseline 且前一状态不是）→ 逐条 `send_alert`；`fms run` 的 `failed` 非空 → `send_alert("免费模型日报失败源", ...)`（cli.py:117 附近，`--no-commit` 本地运行时也允许发送，无 env 自然跳过）
  - `fms run` 原有行为除新增告警外零变化

- [ ] **Step 1: 写失败测试** `tests/test_cli_vendors.py`

stub 注入：monkeypatch `build_llm_client`/`build_search_provider` 返回 stub；respx mock 两目录 API。用例：①首日+无 LLM/search（stub 为 None）→ 产出空表 md+json+README 区块+基线全 pending，退出 0（Review Focus 4）②事件日：目录新条目 → classify flagship → extract 有效 → 基线更新、报告含该行、commit 调用参数断言（`--no-commit` 下不调 git）③二次确认失败 → 线 pending、不进表 ④冻结事件触发一次 send_alert ⑤同日重跑幂等（已有当日基线时不重复判 LLM）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 `run_vendors` 并挂到 argparse；`run` 增告警两行**
- [ ] **Step 4: 跑测试确认通过** Run: `uv run pytest tests/ -v` 全绿
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): fms vendors 子命令与故障告警接线"`

### Task 9: workflow 集成与收尾

**Files:**
- Modify: `.github/workflows/daily.yml`、`.env.example`、`README.md`（使用节补一句）
- Test: 无新测试（配置文件）

- [ ] **Step 1: daily.yml 在 "Run daily report" step 后新增 step**

```yaml
      - name: Run vendor freshness report
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          BRAVE_API_KEY: ${{ secrets.BRAVE_API_KEY }}
          LLM_API_BASE: ${{ vars.LLM_API_BASE }}
          LLM_MODEL: ${{ vars.LLM_MODEL }}
          LLM_FALLBACK_MODELS: ${{ vars.LLM_FALLBACK_MODELS }}
          ALERT_WEBHOOK_URL: ${{ secrets.ALERT_WEBHOOK_URL }}
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          uv run fms vendors
```

同时给现有 "Run daily report" step 的 env 增加 `ALERT_WEBHOOK_URL: ${{ secrets.ALERT_WEBHOOK_URL }}`。不加 `if: always()`（spec §9 决议）。

- [ ] **Step 2: `.env.example` 追加 `ALERT_WEBHOOK_URL=`（带一行中文注释：故障通知 webhook，POST {title, content}）**
- [ ] **Step 3: README 使用节补一句**：新鲜度日报由同一定时任务生成，需在 Secrets 配置 `ALERT_WEBHOOK_URL`（可选）
- [ ] **Step 4: 全量验证** Run: `uv run pytest tests/ -v` 与 `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/daily.yml'))"` → 全部通过
- [ ] **Step 5: Commit** `git commit -m "feat(vendors): 接入每日 workflow 与告警配置"`

### Task 10: 首日真实干跑（可选，需本地 .env 三个 key）

- [ ] **Step 1:** `uv run fms vendors --no-commit`（真实网络+真实 LLM/Brave；预期首日全厂商复核、产出 `reports/各大厂商大模型新鲜度-<今日>.md/json` 与基线；LLM 配额耗尽的厂商 pending 属预期）
- [ ] **Step 2:** 人工抽查报告 3 行：模型名链接可打开且为官方域、日期与公告一致
- [ ] **Step 3:** 干跑产物不提交（`--no-commit`），确认无误后由当日 Actions 正式跑首日
