# 厂商新鲜度 v3：人工圈定模型清单 + 全自动日期提取 设计文档

- 日期：2026-11-01
- 状态：待实施
- 前置：`2026-10-30-vendor-official-crawl-redesign.md`（v2，官网公告爬取主通道）
- 用户决策（2026-11-01）：**yml 只人工维护"有哪些模型"，发布日 100% 由管线提取**（明确否决了 v2 的自动发现范式与"人工预填日期"逃生口）

## 1. 背景与动机

v2 首跑（2026-10-30 运行，ok=5 / pending=19）暴露的不是单点 bug，而是"自动发现"范式的结构性成本：

1. **LLM 归类是不确定性与配额消耗的根源**：增量期每条门筛未命中公告都要 `classify_entry` 烧一次 LLM；"多线命中取首"只是启发式（cli.py:218 语义）。
2. **两线折叠损失信息且宽正则误归**：每家最多 2 行、每线只留最新一个模型；2026-10-30 实际把 MiniMax M3.1 Flash（目录 id `minimax-m31f`）经旗舰线宽模式 `minimax-m\d` 误归旗舰——模型级窄匹配本可根治。
3. **状态机与事实性质不匹配**：发布日是不可变历史事实，确认一次即永真；v2 的 pending→ok→每日重臂→7 天冻结每天都在为"其实不会变的数据"做核实。
4. **降级 3 家在 v2 里无解**：无公告源 → 永远 pending → 冻结。2026-11-01 复测：`openai.com/news` 带浏览器 UA 仍 403、`platform.openai.com/docs/changelog` 301 跳转、`qwen.ai/sitemap.xml` 302——plain HTTP 通道确认不可行（与 v2 §3 探测一致）。
5. 清单显式化后报告可预期：报告 = 我们维护的模型 × 各自发布日；新增追踪 = 改一行 yml；目录新模型只**告警**不自动纳入。

v2 成果全部保留复用：四模式公告解析器（`vendor_source.py`）、文章页提取 + 防捏造校验（`vendor_judge.py`，含 2026-11-01 多格式日期归一比对修复）、目录双源采集、⧉ 佐证、webhook 成功/失败日志、-pending 明细报告。

## 2. 目标与非目标

**目标**
- yml 人工维护每家模型清单（key/name/match/exclude），**不含日期、不含 URL**
- 每模型发布日 + 官方链接全自动提取；一次确认永固（confirmed 永不重跑）
- 未维护新模型发现信号：目录新条目不匹配任何已维护模型 → webhook，0 LLM
- LLM 用量：仅"未确认模型且厂商有公告源"触发 1 次/模型/天；稳态 0 次/天

**非目标**
- 不自动纳入新模型（发现只告警，人工决定加清单）
- 不追踪 `:batch`/`:free` 等平台衍生变体（窄模式天然排除）
- 不做发布日之外的字段（参数/定价/上下文长度等）

## 3. 配置模型（data/vendor_releases.yml）

```yaml
vendors:
  - id: anthropic
    name: Anthropic
    official_domains: [anthropic.com]        # 提取校验官方域白名单（沿用 v2）
    directory_slugs: { openrouter: [anthropic], modelsdev: [anthropic] }
    announcements:                            # 沿用 v2 四模式源，只为日期提取服务
      - { name: 新闻 sitemap, url: "https://www.anthropic.com/sitemap.xml",
          mode: sitemap, include: "/news/" }
    models:                                   # ← 人工维护的全部内容，仅此而已
      - key: claude-opus-5-5                  # 稳定标识（基线/报告锚点）
        name: Claude Opus 5.5                 # 报告展示名
        match: ['opus[ -]?5\.5']              # 窄正则 any-of；对公告标题/目录 id（casefold、保留连字符）
        exclude: []                           # 可选：从命中中剔除子串（如 'prime'）
      - key: claude-sonnet-5-5
        name: Claude Sonnet 5.5
        match: ['sonnet[ -]?5\.5']
```

- **删除**：`lines`（flagship/sub）、`stale_days`（grep 确认无消费方，仅配置默认值）、`override`
- **新增** `manual: {date: ..., url: ...}`（可选，per-model）：纠偏逃生口——管线提错时人工钉死，配置即 confirmed（source=manual），不参与日常提取。这是唯一允许人工写日期的位置，默认不使用
- match 必须窄到能唯一定位该模型（m31f 误归即宽线正则之祸）；多模型共享词干时用 exclude 互斥（如 terra/terra-pro）

## 4. 日期提取通道与优先级（每模型按序）

| 优先级 | 通道 | 前置条件 | 产物 | LLM |
|---|---|---|---|---|
| 1 | 公告源文章页提取 | 厂商有 announcements 且列表条目 match 该模型 | 官方日期 + 官方 URL（白名单域校验） | 1 次/模型/天（未确认时） |
| 2 | models.dev `release_date` | 无公告源、或公告列表无匹配条目 | 目录日期 † + ⧉ 平台页链接 | 0 |
| 3 | OpenRouter `created` | 2 无可用值 | 目录日期 † + ⧉ 平台页链接 | 0 |

细则：
- 通道 1 多条目命中 → 取日期最新条目（沿用 v2 bootstrap 种子规则）；**提取失败不降级通道**：失败是暂态（网络/LLM/校验不过），保持未确认、记原因、进当日异常告警、次日重试。**公告源抓取失败/解析零条目同样是暂态**（上游改版不会自愈、偶发 5xx 会自愈，统一按暂态处理）：当日保持未确认、记"公告源不可用"、次日重试——绝不因暂态故障把本可官方确认的模型永久钉死在低精度目录日期上（confirmed 永固，降级不可逆）
- **只有结构性缺席才落目录通道**：公告源抓取成功、解析出的条目集合中确实无该模型匹配条目（典型：厂商本就无公告源；或模型早于列表窗口）
- 通道 2/3 脏数据防护（v2 §2 实测三类：占位符/回填错/快照日）：`release_date` 等于 models.dev 已知占位符 `2024-01-01`、晚于今天、早于 2020-01-01 → 视为缺失走下一通道；OpenRouter `created` 晚于今天同样拒绝
- 目录 id 多个命中（base 与 `:batch`）→ 取无 `:` 的 base id；仍多个 → 取目录日期最新
- 已 confirmed 的模型永不重跑（日期不可变）；纠错走 `manual`
- source 三值显式落基线与报告：`official` / `directory` / `manual`

## 5. 数据流

**首跑**（`data/vendor-models/` 无历史基线）
- 全部模型未确认 → 逐模型按 §4 提取（有公告源 5 家 ≈ 12-16 次 LLM，一次性）
- `seen_directory_ids` 静默播种当日全量匹配+不匹配条目（首跑不做发现告警）

**增量**（有历史基线）
- confirmed 模型零成本跳过
- unconfirmed 模型按 §4 重试
- 目录新条目（不在 seen）：匹配任一已维护模型 → 仅入 seen（⧉ 佐证渲染时实时匹配，不需预存）；**不匹配任何已维护模型 → webhook「发现未维护新模型」**（每厂商每日至多一条，列前 3 个 id）
- 公告列表抓取失败/解析零条目 → 降级日志沿用 v2；该厂商未确认模型当日保持未确认（§4 暂态规则），**不落目录通道**
- 公告条目不匹配任何已维护模型 → 静默忽略（清单即追踪范围；发现信号只来自目录）

## 6. 状态与产物

- **基线** `data/vendor-models/{date}.json`（新目录，schema_version: 1）：

```json
{"schema_version": 1, "date": "...",
 "vendors": [{"id": "anthropic", "seen_directory_ids": [...],
   "models": [{"key": "claude-opus-5-5", "name": "Claude Opus 5.5",
     "date": "2026-09-22", "url": "https://www.anthropic.com/news/...",
     "source": "official", "status": "confirmed", "reason": "", "last_verified": "..."}]}]}
```

  旧 `data/vendor-releases/*.json` 留作 v2 历史，**不再读取**（新目录彻底避免旧格式加载错误）
- **报告** `reports/各大厂商大模型新鲜度-{date}.md` / `.json`：四列表 `| 厂商 | 模型 | 发布日 | 天数 |`，仅 confirmed 进表；组内配置序；目录日期带 `†`，图例注明"目录收录/策展日（models.dev/OpenRouter），可能滞后官方公告数日"；模型链接 = 官方 URL，无官方 URL（目录来源）时链 ⧉ 平台页（⧉ 构造：优先 `https://openrouter.ai/{id}`，模型不在 OpenRouter 时用 `https://models.dev/{provider}/{model}`）；JSON `models[]` 增 `key`/`source` 字段
- **-pending.md**：unconfirmed 模型清单 + 原因（沿用 2026-11-01 机制，按模型重构）
- README 常驻区块同表结构更新

## 7. 告警与日志

| 告警 | 触发 | 状态 |
|---|---|---|
| 发现未维护新模型 | 目录新条目不匹配任何已维护模型（增量期） | 新增（0 LLM） |
| 大模型新鲜度厂商异常 | 公告条目命中但提取/校验失败、单厂商处理异常 | 沿用 2026-11-01 摘要告警，语义按模型 |
| 大模型新鲜度日报失败 | 顶层未捕获异常 | 沿用 |
| 冻结告警 | — | **删除**（无冻结态） |

`send_alert` 成功/失败/跳过日志沿用；每模型提取结果一行日志（通道/结果/原因）沿用 v2 风格。

## 8. 删除与保留清单

**删除**：`classify_entry`、`LineConfig`/flagship/sub、`apply_judgment`/`finalize_state`/`pending_days`/`PENDING_FREEZE_DAYS`、bootstrap 种子逻辑、`override`（被 `manual` 取代）、`stale_days`、`seen_announcement_urls`（v3 公告列表只被未确认模型每日查询，confirmed 永不重跑，无需公告去重记忆；公告条目级 seen 随状态机一起消失）
**保留**：`vendor_source.py` 四模式解析器与 `fetch_listings`、`extract_from_article` + `SYSTEM_PROMPT` + `validate_judgment`（多格式日期归一）、`collect_directory_entries`（扩展：同时返回 openrouter `created` 与 modelsdev `release_date` 原始日期值）、⧉ 佐证、-pending 明细、`_git_commit` 提交链路

## 9. 初始清单（8 家 22 模型；2026-11-01 OpenRouter 目录实测打底，用户可增删）

| 厂商 | key | 展示名 | match（草案） | 目录参考日 |
|---|---|---|---|---|
| openai | gpt-6-1-terra-pro | GPT-6.1 Terra Pro | `gpt[ -]?6\.1[ -]?terra[ -]?pro` | 2026-10-26 |
| openai | gpt-6-1-terra | GPT-6.1 Terra | `gpt[ -]?6\.1[ -]?terra`（exclude pro） | 2026-10-26 |
| openai | gpt-6-1-sol-pro | GPT-6.1 Sol Pro | `gpt[ -]?6\.1[ -]?sol[ -]?pro` | 2026-09-29 |
| anthropic | claude-opus-5-5 | Claude Opus 5.5 | `opus[ -]?5\.5` | 2026-09-22 |
| anthropic | claude-sonnet-5-5 | Claude Sonnet 5.5 | `sonnet[ -]?5\.5` | 2026-09-28 |
| anthropic | claude-haiku-5-5 | Claude Haiku 5.5 | `haiku[ -]?5\.5` | 2026-10-07 |
| google | gemini-4-argon | Gemini 4 Argon | `gemini[ -]?4[ -]?argon` | 2026-10-13 |
| google | gemini-3-8-flash | Gemini 3.8 Flash | `gemini[ -]?3\.8[ -]?flash` | 2026-09-02 |
| deepseek | deepseek-v4-1-flash | DeepSeek V4.1 Flash | `deepseek[ -]?v4\.1[ -]?flash` | 2026-10-17 |
| deepseek | deepseek-v4-pro-0813 | DeepSeek V4 Pro (0813) | `deepseek[ -]?v4[ -]?pro` | 2026-08-12 |
| deepseek | deepseek-v4-flash-0731 | DeepSeek V4 Flash (0731) | `deepseek[ -]?v4[ -]?flash` | 2026-07-31 |
| qwen | qwen3-8-max-prime | Qwen3.8 Max Prime | `qwen[ -]?3\.8[ -]?max[ -]?prime` | 2026-09-23 |
| qwen | qwen3-8-max | Qwen3.8 Max | `qwen[ -]?3\.8[ -]?max`（exclude prime） | 2026-09-03 |
| qwen | qwen3-8-flash | Qwen3.8 Flash | `qwen[ -]?3\.8[ -]?flash` | 2026-08-26 |
| moonshot | kimi-k3-1-pro | Kimi K3.1 Pro | `kimi[ -]?k3\.1[ -]?pro` | 2026-10-23 |
| moonshot | kimi-k3 | Kimi K3 | `kimi[ -]?k3`（exclude 3.1/3-1） | 2026-07-16 |
| zhipu | glm-5-3 | GLM-5.3 | `glm[ -]?5\.3`（exclude flash/flashx/prime/air） | 2026-08-18 |
| zhipu | glm-5-3-prime | GLM-5.3 Prime | `glm[ -]?5\.3[ -]?prime` | 2026-09-23 |
| zhipu | glm-5-3-flash | GLM-5.3 Flash | `glm[ -]?5\.3[ -]?flash`（exclude flashx） | 2026-08-26 |
| minimax | minimax-m3-1-flash | MiniMax M3.1 Flash | `minimax[ -]?m3[.-]?1[ -]?flash`、`minimax-m31f` | 2026-10-19 |
| minimax | minimax-m3 | MiniMax M3 | `minimax[ -]?m3`（exclude m3.1/m3-1/m31） | 2026-05-31 |

（22 项里含备选；实现期按公告源实测微调 match，原则不变：窄、互斥。）

## 10. 测试要点

- config：models 必填/正则可编译/manual 结构校验；lines/stale_days 字段拒绝（向后不兼容显式报错）
- 通道优先级：公告命中不落目录；公告列表无匹配条目落 models.dev；占位 `2024-01-01`/未来日期拒绝后落 OpenRouter；`:batch` 取 base id
- 一次确认永固：confirmed 模型在新公告/新目录数据下不动、零 LLM
- 发现告警：不匹配新条目 → webhook 且入 seen；首跑静默播种；匹配条目只入 seen 不告警
- manual：配置即 confirmed、source=manual
- 提取失败：不降级目录通道、进异常摘要告警、次日重试（unconfirmed 保持）
- 报告/JSON/pending：† 标注、来源字段、组内配置序、空报告警告
- 告警成功日志沿用（test_alert 已覆盖）
