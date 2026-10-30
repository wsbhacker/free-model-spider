# 厂商新鲜度 v2：官网公告爬取主通道（vendor-official-crawl）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **回溯补写声明（2026-10-30）：** 本计划未在实施前编写——实施于同日完成并提交（commit `600a121`），未走 brainstorming → spec → plan → 执行的规范流程即动手。此处按实际落地内容补齐任务分解留档（复选框记录实际完成状态），供后续同类重构对照；若用于复现，以 git 历史 `600a121` 的实际 diff 为准。

**Goal:** 将 `fms vendors` 的日期来源从"Brave 官方域搜索 + LLM 提取"（v1 方案 D，首跑 24/24 全 pending 证伪）替换为"官网公告页爬取 + 文章页 LLM 一次提取"；目录双源降级为触发信号与 ⧉ 佐证；厂商清单收窄 8 家；日常 LLM 调用从 329+ 降至个位数。

**Architecture:** 每厂商配置官方公告源（`announcements: [{name, url, mode, include?}]`，四种通用解析模式 sitemap/headings/links/json）；列表条目经 per-line 门筛定位产品线（歧义时 LLM classify），抓文章页原文由 LLM 提取 model/release_date（防捏造 prompt + evidence 日期校验 + 域名白名单不变）；`apply_judgment`/`finalize_state` 状态机原样沿用；首跑 bootstrap 每线取日期最新门筛条目做种子（至多 1 次提取/线）。

**Tech Stack:** 零新依赖（httpx/pydantic/pytest/respx 全复用）；`BRAVE_API_KEY` 从 vendors 链路移除。

**Spec:** `docs/superpowers/specs/2026-10-30-vendor-official-crawl-redesign.md`

## Global Constraints

- 沿用 v1 全部硬约束：文件名 verbatim、`request_with_retries` 统一出口、Asia/Shanghai 时区、防倒退/未来日期拒绝、极简报告。
- 新增：公告条目 seen 键 = `url::标题小写`（headings/json 模式条目共享列表页 URL）；裁决阶段只 `apply_judgment`，`finalize_state` 统一在厂商收尾循环（冻结告警恰一次）。
- 测试不打真实网络：列表页/文章页/目录 API 全 respx 打桩，fixture 复刻实测页面形态。

## Review Focus（真实数据坑与钉死测试）

1. **门筛 exclude 并集误杀次旗舰线**（v1 遗留 bug：9 家 sub 线候选被旗舰 exclude 全灭）→ per-line 门筛 + `test_screen_new_entries_per_line_exclude_does_not_kill_other_lines`。
2. **headings 模式同 URL 条目被 seen 整页误吞** → 复合键 + 场景②增量测试。
3. **裁决阶段 finalize 抢先冻结、收尾告警丢失** → 统一收尾 + 场景⑦恰一次告警。
4. **bootstrap 提取失败永久卡死** → 失败条目不入 seen 次日自愈 + 场景④。
5. **sitemap 字母序截断丢最新条目** → 日期降序后再截断 + `test_parse_sitemap_slug_title_lastmod_and_include_filter`。

---

### Task 1: 配置模型扩展与 8 家 yml ✅

**Files:** Modify `src/free_model_spider/core/vendor_config.py`（`AnnouncementSource{name,url,mode,include}`）、`data/vendor_releases.yml`
**Tests:** `test_vendor_config.py`（EXPECTED_IDS 15→8）
**Notes:** mode 四值 Literal；announcements 默认空表（降级厂商语义）。阶跃星辰用户决策移除。

### Task 2: 四模式公告源解析器 ✅

**Files:** Create `src/free_model_spider/core/vendor_source.py`
**Tests:** Create `tests/test_vendor_source.py`（13 例：sitemap 排序截断/include 过滤/slug 保留连字符；headings 三家实测形态 deepseek h2+h3 / google strong / zhipu id 日期节+锚 slug；links 单双引号+日期窗口+域名过滤；json title+日期键对；fetch_listings 多源合并去重+单源失败降级）
**Notes:** 解析零候选 → 上层告警沿用昨日基线（上游改版兜底）。

### Task 3: 状态层扩展 ✅

**Files:** Modify `src/free_model_spider/core/vendor_stats.py`
**Tests:** Modify `tests/test_vendor_stats.py`（删 detect_review/update_seen 共 13 例；新增 per-line 修复钉子与 seen 字段 roundtrip）
**Notes:** `VendorState.seen_announcement_urls`；`line_matches` 抽出；`screen_new_entries` 改 per-line。状态机（apply_judgment/finalize_state）零改动。

### Task 4: 文章页提取替代搜索提取 ✅

**Files:** Modify `src/free_model_spider/core/vendor_judge.py`
**Tests:** Modify `tests/test_vendor_judge.py`（删 extract_line/confirm_from_page 共 20 例；新增 extract_from_article 9 例：validate 通过/抓取失败/截断/坏 JSON/LLMError/无日期/校验失败）
**Notes:** SYSTEM_PROMPT/validate_judgment/classify_entry 原样沿用；classify 入参从目录 id 改公告标题。

### Task 5: 编排重写 ✅

**Files:** Modify `src/free_model_spider/cli.py`（`_bootstrap_vendor`/`_process_new_announcements`/`_attach_corroboration`/`_announcement_key`/`run_vendors`）
**Tests:** Modify `tests/test_cli_vendors.py`（9 场景：bootstrap 种子+次日幂等 / 增量 / ignore 入 seen / 提取失败自愈 / 列表降级 / 无 LLM 重试 / 冻结恰一次 / 无公告源审计 / 提交链路）
**Notes:** vendors client 加浏览器 UA（openai 403 实测教训）；目录新条目降级为审计日志；v1→v2 基线迁移 = seen 空自动重跑 bootstrap。

### Task 6: 设计文档与真实端到端冒烟 ✅

**Files:** Create `docs/superpowers/specs/2026-10-30-vendor-official-crawl-redesign.md`（含 spec-check 修订：来源链接/BRAVE 修订/⧉ 口径）
**Notes:** 真实站点全链路冒烟（无 key）：目录采集 + 5 家公告源抓取零降级、rc=0；最终矩阵 = 自动化 5 + 降级 3（openai/qwen/moonshot）。

## 结果

166 tests passed；commit `600a121`。CI 次日 07:03 首次以 v2 运行，预期：5 家自动化厂商 bootstrap 出线（约 10 次提取），3 家降级厂商 pending + 审计日志。
