# 厂商发布间隔统计（vendor-release-stats）设计文档

- 日期：2026-10-29
- 状态：待用户审阅
- 范围：现有仓库新增第二个定时任务（新 CLI 子命令 `fms vendors`），复用现有 LLM/搜索/快照/提交基建

## 1. 背景与目标

每日早晨统计各主流大模型厂商**旗舰与次旗舰系列的最新版本**距今发布多少天，生成独立日报（Markdown + JSON）并在 README 维护常驻汇总表，随 git 提交留档。

**口径**（用户 2026-10-29 决策）：只跟踪旗舰/次旗舰产品线的最新版本（"Gemini 3.7 发布了就不再关注 3.6"）；衍生专用模型（图像/视频/语音/守卫/嵌入等）不算；厂商为固定主流清单（海外+国内）。

**三条核心决策**（用户 2026-10-29 逐条确认）：

1. **数据准确性优先，走官方发布渠道**：日期口径 = 官方公告日；第三方目录（OpenRouter/models.dev）只作"可能有新发布"的廉价事件检测器，**不作为日期来源**。
2. **凡需人工判断/介入的环节，全部由 LLM 自动完成**（判定新条目是否算主要发布、裁决日期冲突、识别旗舰/次旗舰），人工只看报告。
3. 输出为独立日报 + README 常驻表；并入现有每日 workflow。

**非目标（v1 不做）**：历史发布时间线回溯、IM 推送、自动修正官方域配置、追踪开源权重单独口径（HF 目录仅留作未来扩展点）。

## 2. 需求

### 功能需求

1. 每日（北京时间早晨，随现有 07:03 workflow）对固定厂商清单逐家输出：旗舰线最新版本、发布日期、距今天数；有次旗舰线的同样输出。
2. 检测到厂商新发布（旗舰或次旗舰版本更新）时，报告列出"今日变更"（模型、旧→新、来源链接）。
3. 全自动无人值守：LLM 负责全部判断环节；判断不确定时输出"待定"并在报告标注，**禁止编造日期**。
4. 每天照常生成日报与 commit（无论有无变化，与现有日报纪律一致）。
5. 支持手动触发与本地运行（`--no-commit`）。
6. **模型可溯源**：报告正式表中列出的每个模型必须附 ≥1 个官方域内的 URL（指向官方公告/产品页），程序校验通过才允许进表；无官方 URL 的模型只进"待定"区。目录中出现过的模型自动附第三方佐证链接（用户 2026-10-29 要求）。
7. **故障通知**：出现下述 §10.1 定义的故障时，向用户提供的 webhook 发送 `{"title": ..., "content": ...}` JSON（覆盖 `fms run` 与 `fms vendors` 两条流水线）。

### 非功能需求

1. **官方优先**：所有进入报告的日期必须可溯源到官方渠道 URL。
2. **可降级**：Brave/LLM 任一缺失或失败时，沿用昨日基线出数（天数照算），报告标注"未复核"；目录接口失败不影响流程。
3. **防抖**：基线日期只进不退（防 LLM 判定抖动）；版本变更必须附带 ≥ 旧日期的新日期。
4. **成本≈0**：LLM/搜索仅事件触发时调用（无事件平日零调用）。
5. 密钥复用现有 `LLM_API_KEY` / `BRAVE_API_KEY`，无新增密钥。

## 3. 关键调研与选型对比（实测日期：2026-10-29）

### 3.1 候选数据源实测

| 来源 | 字段 | 实测结论 |
|---|---|---|
| OpenRouter 全量 `/api/v1/models`（457 模型） | `created`（上架时间戳） | 厂商覆盖最全；`created` 是上架 OR 时间，实测普遍滞后发布 0-1 天；**Meta 完全失真**（最新条目停留 2025-04）→ 只能当事件门铃 |
| models.dev `api.json` | `release_date`（人工策展） | 日期口径准，但 provider 节点混入第三方模型（如 mistral 节点最新条目是 DeepSeek 模型）、部分厂商滞后约一周、Meta 靠 `meta/muse-wonder-1.0` 这类条目补 → 同样只当事件门铃 + 线索 |
| HuggingFace API | `createdAt` | 开源线日期真实（Qwen 10-09、DeepSeek 10-17 吻合），但噪声多（最新常是 Image 等非 LLM）→ v1 不接，留扩展点 |
| 官方渠道直爬 | 公告页 | 实测 5 家：Anthropic `/news` 静态可解析；Qwen 老博客已迁 qwen.ai；Google/DeepSeek 文档页 JS 渲染拿不到日期；z.ai/news 404 → **15 家逐一写解析器不可行**（本仓库 opencode 源已有上游改版崩溃先例） |
| **官方域限定搜索 + LLM 提取** | Brave `site:` + LLM | 材料 100% 来自官方域名页面，LLM 只做提取判定不做记忆；公告被索引即无滞后 → ✅ 日期唯一来源 |

### 3.2 方案对比（2026-10-29 与用户两轮收敛）

| 方案 | 结论 |
|---|---|
| A. 纯手工配置基线（人工维护日期） | 与决策 2（无人介入）冲突，仅作为配置兜底字段保留 |
| B. 规则匹配多目录自动取数 | 日期滞后 0-7 天且 Meta 失真，不满足"最准"；降级模式复用其目录探测代码 |
| C. LLM+搜索全量每日重判 | 每天全厂商调用有抖动/成本，被 D 取代 |
| **D. 目录当门铃 + 官方域搜索 LLM 裁决（本设计）** | ✅ 采用 |
| E. 官方渠道逐一爬取解析 | 维护量不可接受（§3.1 实测），不采用 |

## 4. 总体架构

```
daily.yml（现有 07:03 北京时间 job，fms run 之后追加一步）
  └─ uv run fms vendors：
     ① load    — 读 data/vendor_releases.yml（厂商/官方域/产品线定义）
                  + 最近基线 data/vendor-releases/YYYY-MM-DD.json
     ② detect  — 事件检测（确定性、零 key）：
                  OpenRouter 全量 + models.dev 中该厂商出现基线未记录的新条目
                  或该厂商两条线均超 stale_days（默认 45 天）未更新
                  → 该厂商进入"待复核"
     ③ judge   — 仅对待复核厂商（LLM 介入点，替代人工）：
                  a. 目录新条目 → LLM 判定属于旗舰线/次旗舰线/不算（衍生专用模型）
                  b. Brave 搜索 site:官方域 → LLM 从官方页标题/摘要/日期提取
                     {线, 模型, 版本, release_date, source_urls}
                  c. 与基线不一致 → 抓取 source_url 原文 → LLM 二次确认日期
                  d. 通过防倒退校验后更新基线；判定不了 → "待定"标注，保留旧基线
     ④ render  — reports/各大厂商大模型新鲜度-YYYY-MM-DD.md（+同名 .json）
                  + README 常驻表（<!-- fms-vendors:start/end --> 区块）
     ⑤ persist — 写当日基线快照 + git commit（复用现有提交纪律）
```

无事件厂商：直接沿用基线算天数（零 LLM 调用）。无 Brave/LLM key 或调用失败：②仍执行，③跳过，状态记入基线快照（报告不呈现，见 §8 极简决议）。

## 5. 配置设计（data/vendor_releases.yml）

```yaml
stale_days: 45            # 超过此天数未更新 → 强制进入待复核（兜底目录盲区如 Meta）
vendors:
  - id: google
    name: Google DeepMind
    official_domains: [blog.google, deepmind.google, ai.google.dev]
    directory_slugs: { openrouter: [google], modelsdev: [google] }
    lines:
      - key: flagship
        name: Gemini 主线
        match: ['gemini-\d+(\.\d+)?']        # 目录条目匹配（regex，any-of）
        exclude: [flash, image, video, embedding, guard]
      - key: sub
        name: Gemini Flash
        match: ['gemini-\d+(\.\d+)?-?flash']
  - id: zhipu
    name: 智谱 AI
    official_domains: [z.ai, bigmodel.cn, zhipuai.cn]
    directory_slugs: { openrouter: [z-ai], modelsdev: [zai, zhipuai] }
    lines:
      - key: flagship
        name: GLM 主线
        match: ['glm-\d+(\.\d+)?']
        exclude: [flash, air, embedding, voice, vision-only]
      - key: sub
        name: GLM Flash
        match: ['glm-\d+(\.\d+)?-?(flash|flashx)']
  # …共 15 家，见 §5.1；增删厂商只改此文件
```

match/exclude 是**目录门铃的粗筛**；产品线归属的最终判定由 LLM 做（`lines[].name` 与 `exclude` 同时写进 LLM prompt 作自然语言依据）。

`override: {model, release_date, source}`（可选，正常不填）：人工纠偏兜底。某线配置了 override 时，①载入即直接采用其值作为该线当日结果，并**跳过**该线的目录事件检测与 LLM 裁决（删除 override 后恢复自动）。

### 5.1 厂商清单（v1 固定 15 家）

| 厂商 | 旗舰线 | 次旗舰线 |
|---|---|---|
| OpenAI | GPT 主线 | GPT mini / o 系推理线 |
| Anthropic | Claude Opus | Claude Sonnet |
| Google DeepMind | Gemini 主线 | Gemini Flash |
| Meta | Llama 主线 | — |
| xAI | Grok 主线 | — |
| Mistral | Mistral Large | Mistral 中杯（Medium/Ministral） |
| Cohere | Command 主线 | — |
| DeepSeek | DeepSeek V/R 主线 | DeepSeek Flash |
| 阿里 Qwen | Qwen Max/旗舰 | Qwen Turbo/Flash |
| 月之暗面 | Kimi K 主线 | Kimi K Flash/轻量 |
| 智谱 AI | GLM 主线 | GLM Flash |
| MiniMax | MiniMax M 主线 | M Flash |
| 字节 Seed | Seed/豆包主力 | — |
| 腾讯混元 | Hunyuan 主线 | — |
| 阶跃星辰 | Step 主线 | — |

（线的具体命名与 match 规则在实施计划中按各厂商当前实际产品线细化，上表为口径范围。）

## 6. 事件检测规则（确定性层）

1. **新条目检测**：拉取 OpenRouter 全量 API + models.dev `api.json`（均无 key、仓库已有请求/重试封装）。对每厂商：取其 `directory_slugs` 下所有条目 id/name，按 `lines[].match+exclude` 粗筛出候选，与基线快照中该厂商的 `seen_directory_ids`（已见目录条目集合，随基线逐日滚动更新）比对，出现新候选 → 待复核。
2. **陈旧兜底**：厂商任一线满足 `today - max(release_date, last_verified) > stale_days` → 待复核。复核后无论有无新发布都刷新该线 `last_verified`（**重臂一个完整窗口**），确保无发布厂商每 `stale_days` 天最多复核一次，守住"平日 0 调用"（覆盖 Meta 类目录盲区：目录没条目但官方可能已发）。
3. 首日运行无基线：全部厂商进入待复核，当日完成首次官方域确认并建立基线。首日调用量约 15 厂商 × 2~4 次，可能超 LLM 免费档日限额——**不特殊处理**：配额耗尽的厂商自动落为 pending，靠 §10 的 pending 每日重试机制跨日自然续跑补齐。

## 7. LLM 裁决管线（替代人工介入层）

对每个待复核厂商，按线依次：

1. **条目归类判定**（仅当由新目录条目触发）：输入目录新条目 id/name + 该厂商产品线定义 → 输出 `flagship | sub | ignore`（ignore = 衍生专用/第三方挂靠模型，附一句理由）。ignore 不推进基线，判定记入基线快照审计字段（报告不呈现）。
2. **官方域搜索**：Brave `site:官方域1 OR site:官方域2 厂商名 旗舰线名 发布`，取前若干条标题+摘要+URL；间隔 ≥1.1s（限速）。
3. **提取判定**：LLM prompt 硬约束"只基于搜索材料，输出 JSON：`{model, version, release_date(YYYY-MM-DD), source_urls[], confidence}`，材料不足则 `release_date: null` 并给 reason"（沿用 intro.py 的防幻觉与解析重试模式，重试 1 次）。
4. **二次确认**：提取结果与基线不一致（新版本/新日期）时，抓取 `source_urls[0]` 页面正文（截断），LLM 从**原文**确认发布日期与模型名；确认失败 → 待定，保留旧基线。
5. **防倒退与变更判定**：新 `release_date` 必须 ≥ 基线日期；基线日期回退一律拒绝（防抖）。变更口径：**以日期移动为准**——日期前进才更新基线（变更记录只入基线，报告不设小节）；同日期仅模型名字符串不同（如 "GLM-5.4" vs "GLM-5.4-Preview"）且经二次确认的，仅作基线命名修正（不计为变更），杜绝字符串抖动。捆绑多条产品族的次旗舰线（如 GPT mini + o 系）：代表条目 = 该线内**最新发布**的那条（日期优先，同日取 LLM 判定），无固定族优先级。

### 7.1 LLM 提示词防捏造硬约束（所有调用点统一，用户 2026-10-29 要求）

`fms vendors` 的**每一次** LLM 调用（条目归类、提取判定、二次确认）共用同一段 system prompt 基座，逐条硬约束：

1. **只准依据给定材料**（搜索结果 / 页面原文 / 目录条目）：材料里没写的信息一律视为不存在，禁止用你训练记忆中的模型发布知识补全、校对或"回忆"。
2. **禁止推算与估算**：日期只允许原样转录材料中出现的日期（归一化为 YYYY-MM-DD）；"大约/最近/一个月前"这类表述不接受，宁可输出 null。
3. **每个事实字段必须给出处**：非 null 的 `model`/`version`/`release_date` 必须附 `evidence`——材料原文摘句或来源 URL；给不出 evidence 的字段强制 null 并在 `reason` 写明缺什么。**URL 证据必须是材料中出现的原始链接，且属于该厂商 `official_domains` 白名单**，禁止自行拼凑/猜测 URL。
4. **不确定就是不确定**：输出 `release_date: null` + `reason`，绝不猜测、绝不编造、绝不"合理推断"。
5. 只输出约定 schema 的单个 JSON 对象，不输出多余内容。

**程序侧校验兜底（不信任提示词）**：解析 LLM 输出后，程序校验：① 每个非 null 事实字段都携带非空 `evidence`；② `release_date` 能从 evidence 文本中找到对应日期字符串（格式归一后比对）；③ `source_urls` 非空且每个 URL 的域名都落在该厂商 `official_domains` 白名单内——**校验不过的模型不得进入报告正式表**，按材料不足降级 pending。此校验沿用并强化现有 `intro.py` 的"禁止编造材料之外的信息"模式。

### 7.2 pending 重试与 seen 集合更新时机（状态机补全）

- `seen_directory_ids` 只收录**已有最终结论**的条目：归类为 ignore（终态），或归属某线且该线裁决完成（ok / 命名修正）。pending 中的条目不进 seen → 次日自动重新触发，避免一次瞬时故障冻结厂商 45 天。
- pending 厂商**每日自动重试，至多连续 7 天**；7 天仍 pending → 冻结（条目入 seen、状态锁定 stale-baseline），等下一次目录新条目或 stale 窗口再唤醒，避免不可解厂商（官方域失效等）永久烧配额。冻结时刻经 webhook 告警（§10.1），报告不呈现。

LLM 用量：平日 0 次；事件日每厂商 2~4 次；无 key/失败全链路可跳过（§4 降级）。

## 8. 输出格式

### 日报 reports/各大厂商大模型新鲜度-YYYY-MM-DD.md

```
# 各大厂商大模型新鲜度 2026-10-29

## 🥬 各大厂商大模型新鲜度

| 厂商 | 模型 | 发布日 | 天数 |
|---|---|---|---|
| OpenAI | [GPT-6.1 Terra](官方公告URL) [⧉](目录佐证URL) | 2026-10-26 | 3 |
|  | [GPT-6.1 mini](官方URL) | 2026-10-26 | 3 |
| Anthropic | [Claude Opus 5.5](官方URL) [⧉](目录URL) | 2026-09-15 | 44 |
|  | [Claude Sonnet 5.5](官方URL) | 2026-09-29 | 30 |
| … | | | |
| 智谱 AI | [GLM-5.3](官方URL) [⧉](目录URL) | 2026-09-24 | 35 |
|  | [GLM-5.3 FlashX](官方URL) [⧉](目录URL) | 2026-09-18 | 41 |

> 模型名链接 = 官方公告/产品页（进表硬条件，域名经白名单校验）；`⧉` = 第三方目录佐证（OpenRouter / models.dev 平台页）

---
基线快照：data/vendor-releases/2026-10-29.json · 口径：官方公告日（Asia/Shanghai）
```

**报告极简（用户 2026-10-29 拍板）**：只有标题 + 一张表 + 两行说明（⧉ 图例、页脚口径行）。不设覆盖描述、今日变更、命名修正、待定/未复核、附注等任何小节；这些过程信息**全部只记入基线快照** `data/vendor-releases/YYYY-MM-DD.json`（审计可溯），其中"pending 连续 7 天冻结"经 webhook 告警（§10.1），不在报告呈现。从未取得官方确认（无可信 URL+日期）的线不出现于报告，确认后自然出现。

排序规则（用户 2026-10-29 拍板）：**厂商列分组**——厂商顺序 = `vendor_releases.yml` 配置顺序（海外在前、国内在后，同 §5.1 清单）；组内旗舰行在前、次旗舰行在后；厂商名仅该组首行显示。天数列不参与排序。

**同名 JSON**（与 md 同文件名，`schema_version: 1` + `date` 头部元信息 + `models[]`）——每个模型一条（用户 2026-10-29 拍板字段）：

```json
{
  "schema_version": 1,
  "date": "2026-10-29",
  "models": [
    {"vendor": "OpenAI", "name": "GPT-6.1 Terra", "url": "https://openai.com/...", "release_date": "2026-10-26"},
    {"vendor": "OpenAI", "name": "GPT-6.1 mini", "url": "https://openai.com/...", "release_date": "2026-10-26"}
  ]
}
```

`url` = 可信地址（官方公告/产品页，域名经白名单校验）。状态类字段（status/last_verified/seen_directory_ids/pending_days）只存基线快照，供次日 diff 与审计，不进报告 JSON。

### README 常驻表

`<!-- fms-vendors:start/end -->` 区块内维护与日报完全相同的厂商分组表 + 一行"更新于 YYYY-MM-DD"，由脚本每日重写（模式同现有 `fms-index` 区块）。

## 9. 调度与提交

- 并入 `.github/workflows/daily.yml`：现有 `fms run` step 之后新增 `uv run fms vendors` step（同一 job、同一 env；新增透传 `ALERT_WEBHOOK_URL` secret，两条流水线共用）。**决议：不加 `if: always()`**——`fms run` 失败即整体红灯人工排查，vendors 次日照跑；避免在异常日基于半成品状态叠加产出。
- commit 复用 `_git_commit` 模式：单独 commit `docs: 大模型新鲜度日报 YYYY-MM-DD [skip ci]`（与现有"免费模型日报"提交信息平行）；同日重跑幂等（覆盖当日快照与报告）。
- 时区 `Asia/Shanghai`，与现有一致。

## 10. 错误处理汇总

| 故障 | 行为 |
|---|---|
| OpenRouter/models.dev 拉取失败 | 该目录检测跳过（另一目录仍可触发）；两家全失败 → 仅靠 stale_days 触发（记入基线，报告不呈现） |
| Brave 失败/未配 key | 跳过裁决，沿用基线，status=pending |
| LLM 429/超时/解析失败 | 退避重试→回退模型→沿用基线（intro.py 同款链路） |
| 二次确认未过 / 材料不足 | 待定标注（pending），保留旧基线，**绝不编造日期**；每日重试，连续 7 天后冻结（§7.1） |
| 防倒退校验不过 | 拒绝更新，报告"待定"并列出被拒的判定供人复看 |
| 官方域搜索无结果 | 同 pending；连续 7 天 pending 的厂商在报告置顶提醒（配置域可能过期） |
| 首日无基线 | 全厂商待复核；若当日 LLM 不可用 → 以 override 或空基线起步，报告全表标 pending |
| git commit 失败 | Actions 红灯，次日重跑自愈（同现有 §11 惯例） |

### 10.1 故障通知（webhook，覆盖 fms run 与 fms vendors）

- **配置**：`ALERT_WEBHOOK_URL`（GitHub Secret / 本地 `.env`；地址可能携带 token，按密钥管理，永不进仓库）。缺失 → 跳过通知，仅 Actions 红灯。可选 `ALERT_WEBHOOK_TOKEN`（同级 Secret）：配置后请求头附 `x-token`（webhook 侧鉴权），未配置不发该头；值同样永不进仓库与日志。
- **契约**：`POST <url>`，body JSON `{"title": string, "content": string}`，中文；content 含：日期、流水线名（run/vendors）、故障对象、异常摘要（截断）、Actions 运行链接（由 `GITHUB_SERVER_URL/REPOSITORY/RUN_ID` 环境变量拼装，本地运行时省略）。
- **触发条件**（与上表对应，只报"需要人看"的，降级不报）：
  1. `fms vendors`：无法产出当日报告/基线（渲染或写盘异常）、git commit/push 失败、厂商 pending 连续 7 天冻结（**仅在状态迁移为冻结那一刻报一次**，不每日重复）。
  2. `fms run`：存在失败源（含源名与异常摘要）、git commit/push 失败。
- **可靠性**：通知是 fire-and-forget——webhook 调用自带重试（沿用仓库退避封装，2 次），最终失败仅打日志告警，**绝不让通知故障影响主流程或退出码**。
- **频控（用户 2026-10-29 拍板：不设每日条数上限）**：每次运行如实上报当日实际发生的全部故障，不做"每天最多 1 条"类限制；同一故障跨日仍存在则当日运行照报。冻结告警在状态迁移发生时发送（冻结是一次性事件，天然不重复）。

## 11. 仓库结构增量

```
src/free_model_spider/
├── core/vendor_stats.py        # 事件检测、防倒退、天数计算（纯函数为主）
├── core/vendor_judge.py        # LLM 裁决管线（归类/提取/二次确认）
├── core/vendor_report.py       # 日报与 README 表渲染
├── core/alert.py               # webhook 故障通知（run/vendors 共用）
└── cli.py                      # 新增子命令 vendors；run 失败路径接入告警
data/vendor_releases.yml        # 厂商配置（进 git）
data/vendor-releases/YYYY-MM-DD.json  # 每日基线快照（进 git）
reports/各大厂商大模型新鲜度-YYYY-MM-DD.{md,json}
tests/test_vendor_*.py
```

## 12. 测试策略

- 单测（respx/mock，不依赖真实网络与真实 LLM）：配置加载与校验；目录新条目检测（含 models.dev 第三方挂靠过滤、exclude 生效）；防倒退（日期回退拒绝、版本变更无新日期拒绝）；天数计算（时区边界）；LLM 判定 JSON 解析与重试；**evidence 校验（无出处的非 null 字段拒收、日期与 evidence 原文不符拒收、全 null+reason 正常放行、source_urls 非官方域拒收且不进正式表、目录佐证链接自动附注）**；二次确认失败保持基线；报告/README golden file；首日无基线路径；降级路径（无 key）；webhook 通知（body 契约、重试、失败不影响退出码、冻结只报一次）。
- 实施走 TDD（仓库惯例）。

## 13. 已知风险

| 风险 | 缓解 |
|---|---|
| LLM 误判/幻觉日期 | §7.1 提示词防捏造硬约束（材料限定官方域 + evidence 强制 + 禁推算）；程序侧 evidence 校验不过即 pending；变更必须二次确认（原文）；来源 URL 必附；防倒退 |
| 判定口径抖动（同一天多次判出不同结果） | 基线 diff + 只进不退 + 待定机制 |
| 官方域列表过期（厂商换域名/新渠道） | 连续 7 天 pending 置顶提醒；域列表集中在 yml 一处可改 |
| 目录 slug 漂移/挂靠噪声 | slug 映射进配置；LLM 归类 ignore 挡挂靠模型 |
| Brave 配额（用户现持有 key，档位未核） | 仅事件触发，平日 0 次、首日约 15 次；真触顶时全链路降级为沿用基线，不影响日报产出 |
| LLM 免费档 50 req/day | 事件日每厂商 ≤4 次，正常远低于顶；沿用回退模型链路 |
| "天数"语义（公告日 vs 可用日） | 口径统一为官方公告日，报告页脚注明 |
| webhook 地址泄露（URL 常含 token） | 只存 GitHub Secret / 本地 .env，不进仓库、不出现在日志与报告 |
| 通知重复（跨日同故障每天各报） | 接受（用户拍板不设每日上限）；冻结类一次性事件天然不重复 |
