# 免费大模型日报（free-model-daily）设计文档

- 日期：2026-09-30
- 状态：待用户审阅
- 范围：新项目 v1（当前仅 OpenRouter 数据源，架构上预留多平台扩展）

## 1. 背景与目标

每日定时抓取 OpenRouter 上的免费大模型列表，与前一日快照对比得出**新增**与**移除**；对每个新增模型生成中文介绍（元数据打底 + 网络搜索 + LLM 整理），以 Markdown 日报形式随 git 提交留档。

**非目标（v1 不做）**：多平台接入（仅预留接口；接入后各平台独立追踪、跨平台不去重，见 §5）、GitHub Pages 看板、IM 推送、实时查询 API。

## 2. 需求

### 功能需求

1. 每日定时（北京时间 08:30 左右）抓取免费模型列表并生成快照。
2. 与昨日快照 diff：报告新增、移除，以及当前总量。
3. 对每个新增模型生成介绍卡片：**不设数量上限**，每个都走完整管线。
4. 日报为 Markdown，**每平台一份文件**，提交进仓库——每天一个 commit（无论当日有无增删，见 §8）。
5. 支持手动触发（workflow_dispatch）与本地手动运行。

### 非功能需求

1. **成本≈0**：数据源、LLM 均走免费通道。
2. **可降级**：搜索、LLM 任一环节失败不影响日报生成，只影响介绍质量。
3. **可扩展**：新增数据源平台只需实现一个接口；LLM 服务可整体替换为任意 OpenAI 兼容服务（含用户的私有服务），仅改环境变量。
4. **密钥安全**：token 只存 GitHub Secrets / 本地环境变量，永不进仓库。
5. **数据真实**：diff 结果忠实反映 API 返回，不做人工过滤（平台自身路由模型除外，见 §6）。

## 3. 关键调研与选型对比（检索日期：2026-09-30）

### 3.1 调度方式

| 候选 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **GitHub Actions cron** | 免费托管（公开仓库无限分钟）、快照/日报随 git 留档、零服务器 | 公开仓库 60 天无 repo 活动会自动停用定时任务；cron 有分钟级~小时级延迟 | ✅ 采用 |
| 本机 cron | 完全本地 | 关机漏跑、留档自理 | 备选（本地手动运行仍支持） |
| 自有服务器/云函数 | 稳定可控 | 有维护成本，用户明确希望仅用 Actions | 不采用 |

来源：[GitHub Docs：Disabling and enabling a workflow](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/disabling-and-enabling-a-workflow)（60 天政策）。缓解：日报 commit 本身即 repo 活动；每月 1 日兜底保活 commit（§10）。

### 3.2 搜索 API（介绍增强用）

| 候选 | 免费额度（2026-09 现状） | 结论 |
|---|---|---|
| **Brave Search API** | 用户已持有 API key | ✅ 首选适配器 |
| Tavily | 约 1000 credits/月持续免费（2026-02 被 Nebius 收购） | 备选适配器（接口已预留） |
| Serper | 仅注册送 2500 次一次性 | 不采用 |
| Brave（新用户） | 免费档已于 2026-02 取消 | 不影响本项目（用户已有 key） |

来源：[搜索 API 对比](https://www.mattcollins.net)、[2026 搜索 API 格局](https://scavio.dev)。注意：Brave 免费档限速 1 req/s，适配器内做请求间隔。

### 3.3 LLM 服务（介绍整理用）

| 候选 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **OpenRouter 免费模型** | 零成本、与项目主题呼应；未充值账户 50 req/day、20 RPM | 50 次/日是硬顶，新增爆发日会部分降级 | ✅ 默认 |
| 私有/付费 OpenAI 兼容服务 | 无配额焦虑、可控 | 有成本 | ✅ 通过环境变量随时切换（同一段代码） |

OpenRouter 免费额度来源：[OpenRouter Free Tier: Real Limits](https://apivale.com)。切换机制见 §9 配置设计。429 处理：指数退避重试 3 次，仍失败该模型降级为纯元数据卡。

### 3.4 运行时与依赖

| 候选 | 结论 | 依据 |
|---|---|---|
| **Python 3.14 + uv** | ✅ 采用 | [Python 3.14.0](https://www.python.org/downloads/release/python-3140/)（2025-10 发布，当前维护版 3.14.7）；[uv](https://github.com/astral-sh/uv/releases) 已是 Python 项目管理主流。注：OpenAI 于 2026 年收购 Astral，uv 仍开源，风险低 |
| Node.js/TypeScript | 不采用 | 无前端同栈需求 |
| Go | 不采用 | 单二进制优势对本场景无意义（跑在 Actions 里） |

依赖刻意最少：`httpx`（HTTP，含超时/连接池）、`pydantic`（模型记录校验）。LLM 与 Brave 均为裸 HTTP 调用（OpenAI-compatible / REST），不引 SDK。测试用 `pytest` + `respx`（httpx mock）。具体版本在实施计划阶段锁定当时最新稳定版。

### 3.5 数据源事实核验（2026-09-30 实测）

- `GET https://openrouter.ai/api/v1/models` **无需鉴权**，返回全量模型（实测 464 个）。
- 免费判定：`pricing.prompt == "0"` 且 `pricing.completion == "0"`（实测 20 个，其中 16 个带 `:free` 后缀，其余为 stealth 限时免费等）。
- 自带元数据：`name`、`description`、`context_length`、模态、`created`（上架时间戳）、`architecture`、定价，介绍管线素材充分。

## 4. 总体架构

```
GitHub Actions（cron 30 0 * * * UTC = 北京 08:30，支持手动触发）
  └─ 单条 CLI 命令跑完全流程：
     ① fetch   — 各数据源拉取免费模型列表（当前仅 openrouter）
     ② diff    — 与昨日快照对比 → 新增 / 移除 / 总量
     ③ enrich  — 逐个新增模型：元数据打底 → Brave 搜索（可选）→ LLM 中文整理（可配置服务）
     ④ report  — 渲染 Markdown 日报 reports/<source>-YYYY-MM-DD-免费模型清单.md（每平台一份，平铺于 reports/，日期为 Asia/Shanghai）
     ⑤ persist — 写快照 data/snapshots/<source>/YYYY-MM-DD.json + git commit（快照与日报同一 commit）
```

## 5. 数据源抽象（多平台扩展点）

- `Source` 接口：`fetch_free_models() -> list[ModelRecord]`，各平台实现一个类并注册到 registry；**日报与快照均按平台独立**：日报平铺为 `reports/<source>-YYYY-MM-DD-免费模型清单.md`，快照按目录 `data/snapshots/<source>/YYYY-MM-DD.json`。
- `ModelRecord` 归一化字段：`source`、`id`（平台原生 id）、`name`、`context_length`、`input_modalities`、`output_modalities`、`created`（Unix 时间戳）、`description`、`links`（平台页等）、`raw`（原始条目，剔除 description 避免重复存储）。
- 未来接新平台：实现接口 → 注册 → 日报自动多一节。
- **设计决策：跨平台不去重**。各平台独立追踪、独立 diff、独立成节——同一模型若出现在多个平台，各平台分别列出，互不合并。

## 6. 快照与 diff 规则

- 只快照免费模型（判定见 §3.5），存 `data/snapshots/<source>/YYYY-MM-DD.json`。
- diff 以**完整模型 id** 为 key：能同时捕捉"上架免费版"与"免费版下架"；报告按完整 id 字母序列出（§8）。
- **排除项**：`openrouter/free`（平台自身的免费路由模型，非真实模型）。
- 首日运行无昨日快照 → 以当日列表为基线建快照，日报格式与无增删场景**完全一致**（§8：一、二部分标"无变化"，第三部分列存量）。
- 快照只在前置 fetch 成功后写入；快照与日报在同一 commit 中，保证一致（失败则该日数据整体缺失，次日 diff 以最近一个有效快照为基准）。

## 7. 介绍生成管线（无上限 + 降级链）

对**每个**新增模型（无数量上限）：

1. **元数据打底**：name / description / context_length / 模态 / created / 平台链接。
2. **Brave 搜索**（`BRAVE_API_KEY` 存在时）：查询 `"<模型名> <厂商>"`，取前几条标题+摘要；请求间隔 ≥1.1s（1 req/s 限速）；失败或未配 key → 跳过本步。
3. **LLM 整理**（`LLM_API_KEY` 存在时）：输入元数据 + 搜索结果，prompt 硬约束"**只基于给定材料整理，材料不足须明说**"；输出结构化 JSON（`summary`、`highlights[]`、`caveat`）；解析失败重试 1 次后降级。调用间 sleep 3s（远低于 20 RPM）；429 指数退避重试 3 次，仍失败 → 该模型降级。

**降级链**（逐模型独立）：完整卡（元数据 + 搜索 + LLM 中文整理）→ 元数据卡（无 LLM，代码拼装结构化字段与 description 摘录，中文标签）。diff 与日报结构永不因增强服务失败而缺失。

LLM 用量提示：OpenRouter 免费档 50 req/day，若新增数量常超预算，方案为一次性充值 $10 解锁 1000/day，或将 `LLM_API_BASE` 指向私有服务——不需要改代码。

## 8. 日报格式

每平台一份，平铺于 `reports/`：`reports/<source>-YYYY-MM-DD-免费模型清单.md`（如 `reports/openrouter-2026-09-30-免费模型清单.md`），中文，**三段式**，每段内模型按 id 字母序排序，**每个模型一律附平台页链接**（如 `https://openrouter.ai/<id>`）：

```
# OpenRouter 免费模型日报 2026-09-30
> 免费 20 个（昨日 19：+2 / -1）

## 🆕 新增 (2)
### provider/model:free
- 卡片：名称、厂商、上下文窗口、模态、上架时间、中文介绍、要点、注意事项、[平台页](链接) / 搜索引用

## 🗑️ 移除 (1)
- 列表：[模型 id](平台页链接)、最后出现日期

## 📋 无变化 (18)
- 表格：[模型 id](平台页链接) ｜ 名称 ｜ 上下文 ｜ 模态（仅继续可用的免费模型，即今日列表减去新增）

底部：快照文件链接
```

排序规则：段内按模型完整 id 字母序（`a` < `z`，不区分大小写）排序。

**无增删日**：日报照常生成——第一、二部分标题标记"无变化"（如 `## 🆕 新增——无变化`、`## 🗑️ 移除——无变化`），第三部分照常列出全部存量免费模型。因此每天一份日报、每天一个 commit。

`README.md` 维护日报索引：按日期倒序的表格（日期、各平台新增/移除数、日报文件链接），由脚本自动更新。

## 9. 配置设计（环境变量）

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `LLM_API_BASE` | 否 | `https://openrouter.ai/api/v1` | 任意 OpenAI 兼容端点，接私有服务只改这里 |
| `LLM_API_KEY` | 否 | — | 缺失则跳过 LLM 步骤（全降级为元数据卡） |
| `LLM_MODEL` | 否* | 实施时选定一个当时可用的 OpenRouter 免费模型 | 主模型 |
| `LLM_FALLBACK_MODELS` | 否 | — | 逗号分隔候选列表，主模型 429/不可用时依次尝试 |
| `BRAVE_API_KEY` | 否 | — | 缺失则跳过搜索步骤 |

*无 LLM key 时功能完整但介绍降级。GitHub Secrets 配置 `LLM_API_KEY`、`BRAVE_API_KEY`；本地运行读 `.env`（gitignore）。

## 10. 调度与仓库运维

- Workflow：`.github/workflows/daily.yml`，cron `30 0 * * *`（UTC）+ `workflow_dispatch`；`permissions: contents: write`；用 `GITHUB_TOKEN` commit 日报+快照+README 索引，message 带 `[skip ci]` 防互相触发。
- 保活：公开仓库 60 天无 repo 活动会停用定时任务 → 每日日报 commit 天然满足活跃要求，无需额外保活机制（fetch 连续多日失败属重大故障，届时人工介入）。
- 时区：快照与日报日期按 `Asia/Shanghai`。
- cron 延迟（分钟级~小时级）接受；手动触发可随时补跑（幂等：同日重跑覆盖当日快照与日报）。

## 11. 错误处理汇总

| 故障 | 行为 |
|---|---|
| 数据源 fetch 失败 | 重试 3 次（指数退避）；仍失败 → 整体失败（Actions 红灯），不写快照不出日报，保持数据一致性 |
| 单模型记录字段异常 | 该模型跳过并在日报"异常"小节列示，不影响其他模型 |
| Brave 失败/未配 key | 跳过搜索，介绍降级 |
| LLM 429/超时/解析失败 | 退避重试 → 依次尝试回退模型 → 降级为元数据卡 |
| 无昨日快照（首日） | 以当日为基线，日报格式同无变化场景（§8） |
| git commit 失败 | Actions 失败告警，数据留存在 runner 日志，次日重跑自愈 |

## 12. 仓库结构

```
free-model-spider/
├── .github/workflows/daily.yml
├── docs/superpowers/specs/          # 本文档
├── src/free_model_spider/
│   ├── sources/ (base.py, openrouter.py, registry.py)
│   ├── search/  (base.py, brave.py)
│   ├── llm/     (client.py)          # OpenAI 兼容客户端
│   ├── core/    (diff.py, snapshot.py, report.py, intro.py)
│   └── cli.py                        # 入口：run / --no-commit
├── tests/                            # pytest + respx，fixture 为缓存的 API 响应
├── data/snapshots/<source>/YYYY-MM-DD.json
├── reports/<source>-YYYY-MM-DD-免费模型清单.md
├── pyproject.toml                    # uv 管理
└── README.md                         # 日报索引 + 使用说明
```

## 13. 测试策略

- 单测：diff 规则（新增/移除/变体分组/排除项/首日基线）、归一化（缺字段容错）、报告渲染（golden file）、介绍降级链（搜索/LLM 各级失败）、快照读写。
- HTTP 全部 respx mock（含 429/超时/坏 JSON 用例）；不依赖真实网络的 CI。
- 实现阶段走 TDD。

## 14. 已知风险

| 风险 | 缓解 |
|---|---|
| LLM 幻觉 | prompt 硬约束"只基于给定材料"；材料=元数据+搜索结果；解析失败降级 |
| OpenRouter 免费 50 req/day 硬顶 | 爆发日自动逐模型降级；可切私有服务（§9） |
| 公开仓库 60 天无活动停用 cron | 月度保活 commit（§10） |
| Actions cron 延迟 | 接受；重要场景手动触发 |
| Brave 限速 1 req/s | 请求间隔 ≥1.1s |
| 快照体积增长 | 仅免费模型、剔除 raw 中重复 description（实测每日约几十 KB，可接受） |

## 15. 未决问题

- 仓库公开/私有：**不阻塞开发**（token 走 Secrets，代码零差异）。默认按公开设计（Actions 免费无限分钟、日报可分享、为未来 Pages 铺路），随时可切换。
