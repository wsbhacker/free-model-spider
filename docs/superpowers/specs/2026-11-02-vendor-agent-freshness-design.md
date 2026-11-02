# 厂商新鲜度 v4：Dify Agent 监测（一次性基线 + 增量更新）设计文档

日期：2026-11-02 · 取代 v3（2026-11-01-vendor-curated-models.md）的提取管线；`fms run`（免费模型日报）不受影响。

## 0. 决策记录

用户已确认（2026-11-02 对话）：

| 决策 | 结论 |
|---|---|
| 研究主体 | 不再由管线直连 LLM + 自建抓取，改为调用外部 agent，由其自主搜索并直接返回答案 |
| 基线策略 | 一次性建立基线后冻结；此后每日只监测"有没有新模型"，有则加入并淘汰其上一代；"每天 +1"由发布日推算 |
| SDK | 不用官方 `dify-client`（PyPI 0.1.10，2023-11-15，基于 requests，`base_url` 写死 `https://api.dify.ai/v1`，流式只返回原始 Response、不解析 SSE、不重试），改用项目已有 `httpx` + `httpx-sse`（0.4.3，零依赖） |
| 淘汰呈现 | 被取代的旧一代从报告与 README 中移除，仅在状态文件留痕（`retired_on` / `superseded_by`） |
| 基线来源 | `fms vendors --bootstrap` 让 agent 逐厂商生成草稿 JSON，用户过目修正后提交即为基线 |
| `vendor_releases.yml` | 用户认为"人工维护模型清单"仍不合理；v4 把模型清单彻底移出配置，配置只剩厂商身份 |

我的判断（用户把"两个服务都现成时选哪个"交给我定，可随时推翻）：

- **选 Dify Agent 应用，而非 Hermes Agent**。理由：① 这是一个要无人值守跑数月的监测任务，Dify 的 Service API 多年稳定、每次调用是全新对话（无记忆漂移）、控制台可逐步回看 agent 思考，"服务形态"优势压过 Hermes 的 agent 能力优势；② Hermes 的浏览器级工具对 openai.com 403 / qwen.ai SPA 这类站点的召回优势，被本设计的"自愈提问窗口"（§7.1：永远问"最新已知发布日之后"，漏一天次日自动补上）大幅抵消；③ Hermes API server 共用整个个人 agent 网关的单一 Bearer key，泄漏半径大于 Dify 的单应用 key；④ Hermes 2026-09 两天 20 个 commit 几乎全是 fix，API 面仍在快速变化。
- 代码里保留 `AgentBackend` 薄接口（§6.1），Dify 为首个实现；若 Dify 下线 legacy Agent 应用、或用户想要 Workflow 类型化输出、或想试 Hermes，各只需新增一个实现文件。

## 1. 背景与动机

v3 让人工在 `data/vendor_releases.yml` 圈定"每厂商有哪些模型"（含窄正则 match/exclude、公告源 URL、目录 slug），管线按"公告页 LLM 提取 > models.dev > OpenRouter"通道优先级提取发布日。实际运行暴露：

- 3 家（openai / qwen / moonshot）公告源因 403 / SPA / JS 渲染不可用，只能退化到目录"上架日"（语义不是发布日）；
- 模型清单、正则、公告源都要人工跟着厂商变化维护，这正是用户不想承担的；
- `data/vendor-releases/` 至今只有一个 2026-10-30 的旧 schema 文件（flagship/sub 两条线），所有 `release_date` 为 `null`——**v3 基线从未落盘，当前不存在可冻结的基线**；
- 直连 LLM 需要在 CI 维护 `LLM_API_KEY / LLM_API_BASE / LLM_MODEL / LLM_FALLBACK_MODELS`，且搜索能力受限。

v4 把"研究"整体外包给一个具备搜索 / 读网页工具的 Dify Agent 应用：管线只负责提出精确的问题（带基线上下文）、校验答案、维护状态、渲染报告。

## 2. 目标 / 非目标 / 反目标

目标：

1. 每日自动回答"各厂商自最新已知模型之后是否发布了新模型"，有则落库、出报告、告警。
2. 人工配置只剩厂商身份（id / 名称 / 官方域名 / 别名），模型清单完全由状态文件承载、由 agent 增量维护。
3. 状态文件只增不删：新增、淘汰均为追加字段，可审计。
4. 延续 v3 铁律——**暂态故障绝不改写状态**：agent 调用失败 / 答案不可解析时，该厂商状态原样保留、告警、次日重试。
5. 一次性基线由 agent 生成草稿、用户审阅后冻结，用户不手写日期。

非目标：

- 不再做 models.dev / OpenRouter 目录交叉验证与"未维护新模型发现"（目录通道整体退场）。
- 不改动 `fms run`、`daily.yml`、`sources/`、`search/`、`llm/`（`llm/` 仍服务 `core/intro.py`）。
- 不在本轮实现 Hermes / Dify Workflow 后端，只留接口。
- 不处理非 LLM 产品线（图像 / 语音 / embedding）——提问契约明确排除。

反目标（做到了也算失败）：

- agent 幻觉条目进入报告：没有官方域名证据 URL 的条目一律不入库。
- 因 agent 偶发失败导致基线被清空或回退。
- 为了"更准"重新引入任何人工维护的模型级配置。

## 3. 选型实据（2026-11-02 检索）

- Dify Agent 应用仅支持 streaming："Legacy Agent and Agent apps support `streaming` only; a `blocking` request fails with 400."（[Send Chat Message](https://docs.dify.ai/en/api-reference/chat-messages/send-chat-message.md)）。Agent 流事件：`agent_message`（answer 增量）、`agent_thought`、`message_end`（`metadata.usage`）、`message_replace`（审核替换全文）、`error`、`ping`。请求体必填 `inputs`、`query`、`user`。
- Dify 已将 Agent 应用标为 legacy；Chatflow / Workflow 有 Agent 节点，可在 End 节点输出类型化变量（[Agent 节点](https://docs.dify.ai/en/guides/workflow/node/agent)）——这是未来 `AgentBackend` 的第二个实现候选。
- Cloudflare 代理读超时 125 秒、非企业版不可调，只约束响应头到达时间，未对分块间隔设限（[Error 524](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)）。Dify 挂在 CF 后面 → blocking 必 524 → **streaming 是唯一可行模式**，与应用类型无关。
- Cloudflare Access 服务令牌：每个请求直接带 `CF-Access-Client-Id` / `CF-Access-Client-Secret` 头，无需换 token；鉴权失败返回 401/403、不重定向（[Service tokens](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/)）。
- `dify-client` 0.1.10 源码（PyPI wheel 实读）：`requests.request(..., stream=stream)` 原样返回，无 SSE 解析、无重试、`base_url` 硬编码、请求头不可扩展。
- `httpx-sse` 0.4.3（2025-10-10），零依赖，提供 `connect_sse` / `iter_sse`（[PyPI](https://pypi.org/project/httpx-sse/)）。
- Hermes Agent（[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)）：`gateway/platforms/api_server.py` 提供 OpenAI 兼容 `POST /v1/chat/completions`、SSE 10 秒 keepalive、Bearer `API_SERVER_KEY`、每请求跑完整工具循环；默认监听 `127.0.0.1:8642`。

## 4. 配置模型：`data/vendors.yml`（取代 `data/vendor_releases.yml`）

```yaml
# 厂商身份；模型清单不在此维护，见 data/vendor-releases/<date>.json
vendors:
  - id: openai                       # 小写 slug，唯一，作为状态文件主键
    name: OpenAI                     # 报告显示名
    official_domains: [openai.com]   # 证据 URL 域名白名单（含子域）
    aliases: []                      # 可选：提问时附带的别名/中文名，帮助 agent 搜索
  - id: moonshot
    name: 月之暗面
    official_domains: [moonshot.cn, moonshot.ai, kimi.com]
    aliases: [Moonshot AI, Kimi]
```

- pydantic `extra="forbid"`（沿用 v3 做法），旧字段（models / announcements / directory_slugs / manual）出现即报错，避免 v3 配置被静默忽略。
- 初始 8 家沿用 v3：openai、anthropic、google、deepseek、qwen、moonshot、zhipu、minimax；`official_domains` 从 v3 迁移，moonshot 补 `kimi.com`（v3 注释已记录 changelog 在 platform.kimi.com）。
- `fms vendors --config` 默认值改为 `data/vendors.yml`。
- 删除 `data/vendor_releases.yml`。

## 5. 状态模型：`data/vendor-releases/<date>.json`（schema 4）

```json
{
  "schema": 4,
  "date": "2026-11-02",
  "vendors": [
    {
      "id": "anthropic",
      "models": [
        {
          "key": "claude-opus-5-5",
          "name": "Claude Opus 5.5",
          "release_date": "2026-08-14",
          "url": "https://www.anthropic.com/news/claude-opus-5-5",
          "added_on": "2026-11-02",
          "retired_on": null,
          "superseded_by": null
        }
      ],
      "last_check": { "date": "2026-11-02", "status": "ok", "detail": "无新模型" }
    }
  ]
}
```

规则：

- `models` 只追加；淘汰 = 设置 `retired_on`（当日）与 `superseded_by`（新模型 key）。`retired_on is None` 的为"在表模型"，报告与 README 只渲染在表模型。
- `key` 由管线从 `name` 生成：casefold → 非 `[a-z0-9]` 连续段替换为 `-` → 去首尾 `-`。同厂商内 key 已存在（无论在表或已淘汰）即视为已知模型，不重复入库。
- `last_check.status ∈ {ok, transient}`，`detail` 为一句话原因；仅作审计，不参与渲染。结构性错误中止整个运行且不写当日基线（§8.1），因此不会出现在 `last_check` 里。
- 厂商集合以 `vendors.yml` 为准：已从 yml 移除的厂商，其状态块随每日复制原样保留（只增不删），既不提问也不渲染；yml 中有而基线中无的厂商按 §8.1 第 3 步处理，不自动播种。
- 加载器校验 `schema == 4`，否则抛错并指出文件路径与"请运行 `fms vendors --bootstrap`"。旧文件 `data/vendor-releases/2026-10-30.json`（无 schema 字段、全 null）随代码变更一并删除（§8.3）。
- 每日运行沿用 v3 的"找 ≤ 当日最新基线 → 复制为当日文件"模式（`find_latest_baseline` / `save_baseline` 保留）。

## 6. Agent 调用

### 6.1 接口

```python
class AgentAnswer(NamedTuple):
    text: str                       # 聚合后的最终回答全文
    usage: dict | None              # message_end.metadata.usage，仅日志

class AgentBackend(Protocol):
    def ask(self, query: str) -> AgentAnswer: ...

class AgentTransientError(RuntimeError): ...   # 超时/5xx/429/连接失败/流异常结束/error 事件
class AgentStructuralError(RuntimeError): ...  # 401/403/400/404、环境变量缺失
```

`build_agent_backend(client) -> AgentBackend`：从环境变量构造 `DifyAgentBackend`；`DIFY_API_BASE` 或 `DIFY_API_TOKEN` 缺失直接抛 `AgentStructuralError`（agent 是唯一来源，缺配置没有降级路径）。

### 6.2 DifyAgentBackend

- 端点：`POST {DIFY_API_BASE}/chat-messages`。`DIFY_API_BASE` 为 Dify 控制台显示的 API 根（含 `/v1`，如 `https://dify.example.com/v1`），末尾 `/` 容忍。
- 请求头：`Authorization: Bearer {DIFY_API_TOKEN}`；`CF_ACCESS_CLIENT_ID` 与 `CF_ACCESS_CLIENT_SECRET` 同时存在时附加 `CF-Access-Client-Id` / `CF-Access-Client-Secret`（只配一个视为结构性错误）；`Accept: text/event-stream`。
- 请求体：`{"inputs": {}, "query": <query>, "response_mode": "streaming", "user": "fms-vendors", "auto_generate_name": false}`。不传 `conversation_id`：每次提问都是全新对话。
- 超时：`httpx.Timeout(connect=15, read=300, write=30, pool=15)`（read 为分块间隔，对齐仓库已有 300 秒 LLM 超时）；另设整体墙钟上限 15 分钟，超出即中止流并抛 `AgentTransientError`。
- SSE 处理（`httpx_sse.connect_sse` + `iter_sse`），按 `data` JSON 的 `event` 字段分发：
  - `agent_message` / `message`：`answer` 追加到缓冲；
  - `message_replace`：缓冲整体替换为 `answer`；
  - `agent_thought`：日志一行（position / tool / tool_input 前 80 字），便于事后对照 Dify 控制台；
  - `message_end`：记录 `metadata.usage`，正常结束；
  - `error`：抛 `AgentTransientError`（携带 status / code / message）；
  - `ping` 及其他：忽略。
  - 流结束却未收到 `message_end` → `AgentTransientError("流未正常结束")`。
- HTTP 状态分类：401 / 403 / 400 / 404 → `AgentStructuralError`；429 / 5xx / 连接错误 / 超时 → `AgentTransientError`。
- 重试：仅对"首字节到达前"的暂态失败（连接错误、429、5xx）重试 1 次，间隔 30 秒；流中途失败不重试（agent 一轮数分钟，由次日定时重试承担）。

### 6.3 Dify 侧前置条件（用户负责，不在本仓库）

- 应用类型 Agent，策略 Function Calling，模型需支持工具调用；工具至少包含网页搜索与网页读取（能渲染 JS 的读取器更好），最大迭代次数建议 ≥ 8。
- 系统提示词要求：只针对用户消息中给定厂商作答；最终回答**只输出一个 JSON 对象**，不加解释；证据 URL 必须来自官方域名；不确定就返回空列表而不是猜测。
- 本仓库的 query（§7）会重复这些约束，双保险。

## 7. 提问契约与回答契约

### 7.1 每日提问（每厂商一次）

query 由管线模板生成，包含：厂商名与别名、官方域名列表、今日日期、在表模型清单（**每条给出 `key` / `name` / `release_date` 三项**，并说明 `supersedes` 须回填其中的 `key`）、提问窗口起点、输出 JSON 结构与硬性规则。

- **窗口起点 = 在表模型中最大的 `release_date`**（同日发布也要能报出，因此是"≥ 起点且名称不在已知清单"）。该厂商无在表模型时（仅在 bootstrap 草稿为空时可能出现），起点 = 今日 − 180 天，并在当日 `-pending.md` 记一行"无在表模型，窗口回退 180 天"。
- 这使提问天然自愈：某日 agent 漏报，次日窗口起点不变，仍会被问到。
- 硬性规则写进 query：只报通用大语言模型新版本/新系列（含推理、多模态文本模型），排除图像 / 视频 / 语音 / embedding 产品，排除已知清单中的模型、排除仅改名或仅降价；每个条目必须给官方公告 URL；无新模型返回 `{"new_models": []}`。

回答契约（pydantic，`extra="ignore"`，agent 多给字段不致失败）：

```json
{
  "new_models": [
    {
      "name": "Claude Opus 6",
      "release_date": "2026-11-01",
      "url": "https://www.anthropic.com/news/claude-opus-6",
      "supersedes": "claude-opus-5-5"
    }
  ]
}
```

- 解析：先剥离 ```` ```json ```` 围栏，再取首个 `{` 到末个 `}` 子串 `json.loads`（复用 v3 `vendor_judge._parse_llm_json` 思路）；失败 → 整厂商 `transient`。
- 逐条校验（任一不过即"拒收"该条，不影响同批其他条目）：
  1. `release_date` 为 ISO 日期，且 窗口起点 ≤ 日期 ≤ 今日（拒收原因：日期越界 / 未来日期）；
  2. `url` 主机名等于或以 `.` + 任一 `official_domains` 结尾（复用 v3 `vendor_judge._host_in_whitelist` 逻辑；原因：证据非官方域名）；
  3. `name` 生成的 key 不在该厂商已有 key 中（否则静默忽略，日志"已知模型"）。
- `supersedes` 为可选，单值（一个新模型只取代一个旧模型；agent 报多个新模型时各自填写）。解析顺序：先按该厂商在表模型 `key` 精确匹配；不中则按在表模型 `name` casefold 精确匹配并换算为 key；仍不中则置空并记日志"supersedes 无法解析：<原值>"（不拒收）。
- 拒收条目进入当日 `-pending.md` 并参与告警，附 agent 原文前 500 字，便于人工判断是否手工入库。

### 7.2 引导提问（`--bootstrap`）

query 要求返回该厂商**当前在售、最新一代**通用大语言模型清单（旗舰及其主要规格变体，排除已被更新一代取代的模型），每项含 `name` / `release_date` / `url`：

```json
{ "models": [ { "name": "...", "release_date": "YYYY-MM-DD", "url": "https://..." } ] }
```

校验同 §7.1 的 1、2（窗口起点取今日 − 3 年），拒收条目只打印不入草稿。

## 8. 数据流

### 8.1 每日 `fms vendors`

1. 读取 `data/vendors.yml`；找 ≤ 今日最新基线（schema 4），不存在 → 结构性错误："请先运行 `fms vendors --bootstrap`"。
2. `build_agent_backend`（缺变量 → 结构性错误）。
3. 对每个 yml 厂商（单厂商 try/except 隔离，沿用 v3）：
   - 基线中无该厂商状态块 → 记入 `failed`（"未建基线，请运行 `fms vendors --bootstrap --vendor <id>`"），不提问、不自动播种，继续下一家；
   - 组 query → `backend.ask` → 解析 → 逐条校验；
   - 接收条目：追加模型（`added_on` = 今日），`supersedes` 解析成功则把旧模型 `retired_on` = 今日、`superseded_by` = 新 key；
   - `last_check` = ok（含新增数量）/ transient（原因）；`AgentTransientError` 与答案不可解析记入 `run_failures`；
   - 其他未预期异常记入 `failed`（沿用 v3 cli.py:201-204 的单厂商隔离）；
   - `AgentStructuralError` 不按单厂商处理：立即中止整个运行（所有厂商必然同样失败），走顶层告警并非零退出，不写当日基线。
4. 在表模型 → 报告行（厂商、模型、发布日、距今天数 = `days_since(release_date, today)`、证据链接），厂商内按发布日倒序。
5. 产物：`reports/各大厂商大模型新鲜度-<date>.md` + `.json`（沿用现有文件名）；存在拒收条目、`run_failures` 或 `failed` 时额外写 `-pending.md`；更新 README `<!-- fms-vendors:start/end -->` 块；保存当日基线；`_git_commit`（现有实现，含 Actions 内 `pull --rebase` 再 push）。
6. 退出码（对齐 v3 cli.py:245-254）：结构性错误 / 顶层未捕获异常 → 先告警再返回 1；`failed` 非空（未建基线厂商、单厂商异常）→ 产物照常提交后返回 1；仅 `run_failures`（暂态）或拒收 → 0（已告警，属预期波动）。

### 8.2 `fms vendors --bootstrap [--vendor <id>]`

1. **未指定 `--vendor`**：要求当前不存在任何 schema 4 基线，否则拒绝并提示"已有基线；重做单厂商用 `--vendor`，整体重做请先手动删除 `data/vendor-releases/` 下的基线文件"。不提供 `--force`——整体覆盖是破坏性操作，由人手动删除文件来表达意图。
2. **指定 `--vendor <id>`**（新增厂商、或重做某一家）：`id` 必须在 yml 中；复制 ≤ 今日最新基线（不存在则从空基线开始），**只替换或插入该厂商的状态块**，其余厂商原样保留。替换意味着该厂商旧的 `retired_on` 留痕被草稿覆盖——这是用户显式指定的单厂商重做，且历史版本仍在 git 中。
3. 对目标厂商引导提问（§7.2），生成 `data/vendor-releases/<date>.json` 草稿：模型 `added_on` = 当日，`last_check = {status: ok, detail: "bootstrap"}`；agent 返回空清单的厂商写入空 `models` 并在标准输出醒目提示。
4. 标准输出打印逐厂商表格（名称 / 日期 / URL / 拒收原因）；**不**写报告、不改 README、不提交。
5. 用户审阅修改 JSON（可增删条目、改日期、补空清单）→ 自行提交，即成基线。

### 8.3 切换顺序

1. 代码变更一次提交：新模块、`data/vendors.yml`、删除 `data/vendor_releases.yml` 与 `data/vendor-releases/2026-10-30.json`、workflow 变量、`pyproject` / `uv.lock`。此时每日 job 会因"无基线"结构性失败并告警——这是预期的，直到基线提交。
2. 用户在 GitHub 配好 `DIFY_*` / `CF_ACCESS_*` 变量后，以 bootstrap 模式手动触发 workflow（§11），下载草稿产物；或在本地导出同名环境变量运行 `uv run fms vendors --bootstrap --no-commit`。
3. 用户审阅草稿，提交为 `data/vendor-releases/<date>.json`，每日 job 自动恢复。

## 9. 报告与 README

- 表格列：厂商 | 模型 | 发布日 | 距今（天） | 证据。去掉 v3 的 `†`（目录日期）与 `⧉`（平台页佐证）标记——v4 所有日期均来自官方公告 URL。
- `-pending.md`：三节——"未建基线 / 处理异常的厂商"、"agent 暂态失败厂商（次日重试）"、"拒收条目（原因 + agent 原文摘录）"；无内容的节省略。
- README 块渲染逻辑不变（`update_readme_freshness` 保留，列定义随上一条调整）。

## 10. 告警与日志

- `发现新模型`：当日有接收条目时发送，列出"厂商 / 模型 / 发布日 / URL / 取代了谁"，附 Actions run 链接。
- `大模型新鲜度厂商异常`：当日有暂态厂商或拒收条目时发送（合并一条），列明细与 run 链接——沿用 v3 的汇总告警形态。
- `大模型新鲜度日报失败`：结构性错误 / 未捕获异常（沿用）。
- 日志：每次 `ask` 打印厂商、耗时、usage（有则）、`agent_thought` 摘要行、接收 / 拒收 / 忽略计数。

## 11. Workflow 与变量

`.github/workflows/vendors.yml` 的 `Run vendor freshness report` 步骤环境变量改为：

| 变量 | 类型 | 说明 |
|---|---|---|
| `DIFY_API_BASE` | `vars` | Dify API 根（含 `/v1`） |
| `DIFY_API_TOKEN` | `secrets` | Dify 应用 API key |
| `CF_ACCESS_CLIENT_ID` | `secrets` | Cloudflare Access 服务令牌 ID |
| `CF_ACCESS_CLIENT_SECRET` | `secrets` | Cloudflare Access 服务令牌 Secret |
| `ALERT_WEBHOOK_URL` / `ALERT_WEBHOOK_TOKEN` | `secrets` | 不变 |

移除 `LLM_API_KEY / LLM_API_BASE / LLM_MODEL / LLM_FALLBACK_MODELS`。`daily.yml` 不动（`fms run` 仍用 LLM）。cron、concurrency group、checkout `ref` 均不变。

`workflow_dispatch` 增加输入 `mode`（choice：`daily` 默认 / `bootstrap`）与可选 `vendor`（字符串，仅 bootstrap 用）。`mode=bootstrap` 时执行 `uv run fms vendors --bootstrap --no-commit [--vendor <vendor>]`，用 `actions/upload-artifact` 上传 `data/vendor-releases/` 为产物 `vendor-baseline-draft`，不提交；schedule 触发恒为 daily。这样用户无需在本地持有 Dify / CF 凭据即可生成草稿。

`pyproject.toml` 新增 `httpx-sse>=0.4`，并更新 `uv.lock`（CI 用 `uv sync --frozen`）。`.env.example`（若存在）同步替换变量名。

## 12. 删除与保留清单

删除：

- `core/vendor_source.py`、`core/vendor_judge.py`、`core/vendor_confirm.py` 及 `tests/test_vendor_source.py`、`tests/test_vendor_judge.py`、`tests/test_vendor_confirm.py`；
- `core/vendor_stats.py` 中 `DirectoryRecord`、`collect_directory_entries`、`discover_unmaintained`、`model_matches`、`valid_directory_date`、`is_variant_record`；`cli.py` 中 `_corroboration_map` 与 `run_vendors` 内目录 / LLM 相关分支；
- `core/vendor_config.py` 的 `ManualRef`、`ModelConfig`、`AnnouncementSource`；
- `data/vendor_releases.yml`、`data/vendor-releases/2026-10-30.json`（随代码变更提交删除，见 §8.3）。

保留 / 改造：

- `vendor_stats.py`：`days_since`、`baseline_path`、`find_latest_baseline`、`save_baseline`、`load_baseline`（加 schema 校验）、`Baseline / VendorState / ModelState`（按 §5 重定义）；
- `vendor_report.py`：行模型与渲染按 §9 调整；`update_readme_freshness` 保留；
- `vendor_config.py`：按 §4 重写；
- 迁入新模块（建议 `core/agent_backend.py` + `core/vendor_agent.py`）：域名白名单判断、JSON 围栏剥离；
- `core/alert.py`、`_git_commit`、`llm/`、`sources/`、`search/` 不动。

## 13. 测试要点

- **DifyAgentBackend（respx 模拟 `text/event-stream`）**：多段 `agent_message` 聚合；`message_replace` 覆盖；`error` 事件 → 暂态；无 `message_end` → 暂态；401/403/400 → 结构性；首请求 503 后重试成功；流中 5xx 不重试；配置 CF 变量时两头都在、未配置时不发；`conversation_id` 不传、`user` 固定。
- **回答解析与校验**：围栏 JSON；非 ISO / 未来 / 窗口前日期拒收；非官方域名（含伪装子域如 `openai.com.evil.net`）拒收；已知模型忽略；`supersedes` 按 key 命中、按 name casefold 命中换算为 key、都不中置空。
- **状态变更**：新增 + 淘汰字段正确；只追加不删除；schema ≠ 4 文件加载报错；窗口起点计算（有 / 无在表模型）；yml 已移除厂商的状态块随日复制保留且不渲染。
- **run_vendors 集成（注入假 backend）**：新模型 → 报告新增、旧模型从报告消失但留在 JSON、`发现新模型` 告警；暂态厂商 → 状态不变、`-pending.md`、异常告警、退出码 0；yml 有而基线无的厂商 → 不提问、进 pending、告警、退出码 1、其余厂商照常；结构性 → 退出码 1 + 失败告警 + 不写当日基线。
- **bootstrap**：无基线时全量生成草稿、不改 README / reports、不提交；已有基线且未指定 `--vendor` 拒绝；`--vendor` 在已有基线上只替换 / 插入该厂商块，其余厂商字节级不变；agent 空清单写入空 `models`。
- **渲染**：距今天数由 `release_date` 推算；厂商内按发布日倒序。
- **配置**：旧 v3 字段出现 → 报错；`id` 重复 → 报错。
- 现有 `tests/test_cli.py::test_git_commit_rebases_onto_advanced_remote_before_push` 等不受影响，全量套件须通过。

## 14. 留给实现者的决定

- query 的具体措辞（满足 §7 契约即可）、中英文选择；
- key 冲突时的后缀策略（建议 `-2`、`-3`）；
- 日志格式、告警正文排版；
- 新模块命名与文件划分；
- bootstrap 标准输出表格格式。

## 15. 风险与应对

| 风险 | 应对 |
|---|---|
| agent 幻觉出不存在的模型 / 日期 | 官方域名证据 URL 强制；日期窗口校验；拒收条目进 pending 由人工复核 |
| agent 漏报 | 窗口起点锚定最新已知发布日，次日自动补报；`发现新模型` 告警让漏报可被人工察觉 |
| Dify legacy Agent 应用被下线 | `AgentBackend` 接口隔离；Workflow 实现是已知路径（§3） |
| CF Access 令牌过期 / 轮换 | 401/403 结构性错误立刻红灯 + 告警，不会静默跑空 |
| 单次 agent 耗时过长 | 分块 read 300 秒 + 整体 15 分钟上限；8 家串行最坏 2 小时，远低于 job 300 分钟 |
| 首跑基线质量 | bootstrap 只出草稿，用户审阅后才成为基线 |
| 同一模型因写法差异（"Claude Opus 5.5" vs "Claude 5.5 Opus"）被当作新模型重复入库 | query 附带已知清单并要求排除；`发现新模型` 告警使重复可被人工察觉，手工在状态 JSON 中标记 `retired_on` 即可修复；不做模糊匹配 |
| 新增厂商后忘记 bootstrap | 每日 run 把该厂商记入 `failed`：进 pending、告警、退出码 1，直到用户执行 `--bootstrap --vendor` |
