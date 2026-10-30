# 厂商新鲜度 v2：官网公告爬取主通道 设计文档

- 日期：2026-10-30
- 状态：实施中
- 前置：`2026-10-29-vendor-release-stats-design.md`（v1，方案 D：目录门铃 + 官方域搜索 LLM 裁决）
- 范围：重构 `fms vendors` 裁决管线的数据获取层；状态机/报告/告警/提交沿用 v1

## 1. 背景：v1 首跑事故与方案 D 的证伪

2026-10-30 首次 CI 运行（commit `48e83c7`）：24 条线全部 pending、报告空表，LLM/搜索额度按首跑规模消耗。经当日实测复盘，方案 D 的三处结构性缺陷：

1. **提取环节的日期门槛放错位置**：`extract_line` 要求搜索摘要中出现日期（`vendor_judge.py` validate：日期必须出现在 evidence 文本），但现实里日期只存在于文章页正文——摘要无日期 → 合规 LLM 必然输出 null → pending。实测 `site:openai.com OpenAI GPT 主线 发布` 命中官方公告页但摘要零日期；`site:z.ai OR site:bigmodel.cn 智谱 AI GLM 主线 发布` 直接 0 结果。
2. **中文 query 打英文官网**：海外厂商官网为英文站，中文线名（"GPT 主线"）+ "发布" 基本搜不出内容。
3. **首跑成本爆炸**：空基线下目录全量条目均为"新条目"，实测粗筛候选 329 个 → classify 329 次 + extract 24 次（坏 JSON 翻倍）。
4. **粗筛 exclude 并集 bug**（`vendor_stats.py` `screen_new_entries`）：所有线的 exclude 合并过滤，旗舰线 exclude 中的次旗舰关键词（flash/mini/sonnet…）把 9 家厂商的 sub 线候选全部误杀 → 次旗舰线永远空。

另实测（2026-10-30）：`openai.com` 新闻页 403 反爬、`anthropic.com` 公告页为 SPA（前 8000 字符无日期）→ confirm 环节在部分厂商同样不可用。

**推特/X 通道**（用户 2026-10-30 提出并否决）：各厂商确在 X 发布公告（@OpenAI/@AnthropicAI/@GoogleDeepMind/@deepseek_ai/@Kimi_Moonshot/@zai_org/@Alibaba_Qwen/@MiniMax_AI），但 X API 读取档 $200+/月、爬取违反 ToS、历史不可回溯（首跑初始化不靠谱）→ 不采用。

## 2. 目录日期双源实测（2026-10-30，触发与佐证用，不作日期真值）

- OpenRouter `/api/v1/models` 的 `created` = **上架 OpenRouter 时间**（官方文档），非发布日；新模型当天上架≈发布日，存量模型为回填值（gpt-4 显示 2023-05-28，真实 2023-03-14），且存在提前占位（deepseek-v4-flash 占位 2026-04-24，实际首发 2026-07-31）。
- models.dev `api.json` 的 `release_date` = 社区手工策展（sst/models.dev 仓库逐模型 TOML）；近期模型准确（gpt-6.1-terra=2026-10-26 与官方公告日一致），存量有占位符（2024-01-01）/回填错/快照日（deepseek-v4-flash 记 10-17 更新日）三类脏数据。
- 两源近半年共有模型 42 个：UTC 同日 62%、±1 天 74%（±1 天多为时区伪差，方向因厂商地域而异）；差 >1 天的 11 个全部可归因（占位/滞后上架/快照日）。

**结论**：目录双源**只做**（a）新条目触发信号（上线即知，免费）；（b）报告 ⧉ 佐证链接；（c）与官网日期交叉验证。日期真值唯一来源 = 官网公告页。

## 3. 官网公告源实测与最终矩阵（2026-10-30，含解析模式选型）

实测发现各站结构差异远超预期（JS 渲染/SPA 壳/重定向壳），解析器按源配置 **mode**：
`sitemap`（loc+lastmod）/ `headings`（日期节标题+模型子节/strong/锚点）/
`links`（锚点+邻近日期）/ `json`（script 内 title+日期键对）。

| 厂商 | 公告源 | mode | 实测结论（2026-10-30） |
|---|---|---|---|
| anthropic | `anthropic.com/sitemap.xml` include=/news/ | sitemap | 568 lastmod、slug 含模型名（claude-opus-4-1 等）✓ |
| google | `ai.google.dev/gemini-api/docs/changelog` | headings | h2=英文日期节、strong=模型条目 ✓ |
| deepseek | `api-docs.deepseek.com/updates` | headings | h2="Date: ISO"、h3=模型名 ✓ |
| zhipu | `docs.z.ai/release-notes/new-released` | headings | div id=日期节、锚点 slug=模型名 ✓ |
| minimax | `minimax.io/sitemap.xml` include=/news\|blog/ | sitemap | lastmod 新鲜、slug 门筛命中 ✓ |
| openai | — | — | changelog 静态 HTML 仅 2025 旧条目（新条目 JS 渲染）、主站/帮助中心 403 → **降级** |
| qwen | — | — | qwenlm.github.io/blog 已变跳转壳（迁 qwen.ai）、qwen.ai 全站 SPA（robots/sitemap 返回 HTML）→ **降级** |
| moonshot | — | — | platform.kimi.com changelog 正文日期不落 HTML（JS 渲染）→ **降级** |

- **降级厂商语义**：无自动裁决；目录新条目审计日志提示人工关注；`override` 手工兜底；pending 满 7 天冻结告警链路照走。
- **阶跃星辰**：用户 2026-10-30 决策移除（无可用公告源）。meta/xai/mistral/cohere/bytedance/tencent 一并移出 v2（v1 配置见 git 历史）。
- 最终范围 **8 家**：自动化 5（anthropic/google/deepseek/zhipu/minimax）+ 降级 3（openai/qwen/moonshot）。

> v1 文档 3.1 "官方页拿不到日期/15 家逐一写解析器不可行" 的结论基于不同 URL 的探测，本次按新入口推翻；解析器是四种通用模式而非每家一个 selector，上游改版由"解析零候选 → 告警"兜底（沿用 opencode 源的教训）。

## 4. v2 架构

```
目录双源（免费）──→ 新条目触发（任一厂商有新条目 → 复核该厂商）
                                        │
官网公告列表页（每厂商 1-2 个 URL）──→ 通用解析器产出 (标题, URL, 日期) 候选
                                        │
                    新公告 URL（未入 seen）→ 行归属：per-line 正则门筛，歧义 LLM 归类
                                        │
                    抓文章页原文 → LLM 提取 model/release_date（防捏造 prompt 沿用）
                                        │
                    validate（官方域白名单 + 日期在 evidence）→ apply_judgment（v1 状态机不变）
                                        │
                    报告/基线/README/提交/告警（v1 不变）
```

核心变化：

1. **Brave 搜索从 vendors 链路移除**：`BRAVE_API_KEY` 不再是 vendors 依赖（`fms run` 介绍功能不受影响）。
2. **列表页解析器是通用启发式**（新 `core/vendor_source.py`）：剥 script/style 后提取 `<a href>` + 锚文本 + 就近日期文本（ISO/中文/英文三种格式），产出自站内、白名单域内的候选条目。**不写每家一个的 selector**。
3. **LLM 只处理"新公告条目"**：日常成本 = 新条目数 × (1 归类[仅歧义时] + 1 提取)，无事件零调用；首跑 bootstrap = 每线取列表页日期最新的一条做种子（fetch + 提取一次），其余老条目直接入 seen，**杜绝 v1 首跑 329 次调用的重演**。
4. **粗筛修复**：行归属按线独立筛（match/exclude 只用本线的），候选并集交给 LLM 归类兜底。
5. **状态机沿用 v1**：`apply_judgment`/`finalize_state`/pending 7 天冻结/防倒退/未来日期拒绝，全部不动；`VendorState` 新增 `seen_announcement_urls` 审计字段（键 = `url::标题小写`，headings/json 模式条目共享列表页 URL 必须带标题区分）。裁决阶段只 `apply_judgment`，`finalize_state` 统一在本厂商收尾循环（冻结迁移告警恰一次）。
6. **配置**：`vendor_releases.yml` 收窄为 8 家（用户 2026-10-30 决策：御三家 + deepseek/kimi/glm/qwen/minimax；阶跃星辰移除），每厂商新增 `announcements: [{name, url, mode, include?}]`。
7. **日期口径冲突**：文章页提取日期与列表页日期不一致时，以文章页为准（进 evidence），差异记 reason 审计。
8. **bootstrap 补充语义**：LLM 未配置或列表解析为空 → 不入 seen，条件恢复后整体重跑；种子提取失败 → 该条目不入 seen，次日走增量路径重试（自愈）。

## 5. 需求（沿用 v1 §2 全部功能/非功能需求，修订如下）

- 修订 1：日期来源从"官方域限定搜索"改为"官网公告列表页 + 文章页原文"；搜索/Brave 移除。
- 修订 2：厂商清单收窄 15 → 8（meta/xai/mistral/cohere/bytedance/tencent/stepfun 移出 v2，配置保留注释便于回加）。
- 修订 3：新增首跑 bootstrap 行为——列表页解析成功的厂商按线种子初始化，解析失败的厂商转 pending + 告警。

## 6. 风险与兜底

| 风险 | 兜底 |
|---|---|
| 官网列表页改版（启发式解析失效） | 解析零候选 → 告警"列表解析失败"，沿用昨日基线出数（v1 降级纪律） |
| openai.com 403 | 已换 platform.openai.com 子域（白名单内）；文章页 403 → 该条目 pending |
| SPA 列表页（google 部分条目） | changelog 页有静态日期；解析零候选走告警 |
| LLM 提取抖动 | validate 硬校验 + 防倒退状态机（v1 不变） |
| 目录日期脏数据 | 只做触发/佐证，不入基线 |
