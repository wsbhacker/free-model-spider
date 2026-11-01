# 实施计划：厂商新鲜度 v3（人工圈定模型清单 + 全自动日期提取）

- 日期：2026-11-01
- 设计文档：`docs/superpowers/specs/2026-11-01-vendor-curated-models.md`（下称 spec）
- 策略：T1→T6 顺序实施，全部完成、全量测试绿后**单次提交**（v3 是一个原子转向：config/状态/管线/报告/yml 必须同时切换，中间态不可运行）；提交按路径精确 add，不 push

## T1 配置层

**文件**：`src/free_model_spider/core/vendor_config.py`、`tests/test_vendor_config.py`

- 新增 `ModelConfig`：`key: str`、`name: str`、`match: list[str] (min_length=1)`、`exclude: list[str] = []`、`manual: ManualRef | None = None`；`ManualRef {date: str, url: str}`
- `VendorConfig`：删 `lines`，加 `models: list[ModelConfig] (min_length=1)`；validator 改为校验 **key 唯一**（不再要求 flagship 在前）
- 删 `LineConfig`、`Override`；`VendorReleasesConfig` 删 `stale_days`
- 测试：models 缺失/空拒绝、key 重复拒绝、manual 结构解析、旧字段（lines/stale_days）混入时的报错可读、yml 加载 golden

## T2 状态与目录采集

**文件**：`src/free_model_spider/core/vendor_stats.py`、`tests/test_vendor_stats.py`

- `ModelState {key, name, date: str|None, url: str|None, source: Literal["official","directory","manual"]|None, status: Literal["confirmed","unconfirmed"]="unconfirmed", reason: str="", last_verified: str|None}`
- `VendorState {id, seen_directory_ids, models}`（删 `seen_announcement_urls`、`lines`）；`Baseline {schema_version: 1 (literal), date, vendors}`
- 基线读写迁到 `data/vendor-models/{date}.json`：`baseline_path`/`find_latest_baseline`/`save_baseline`/`load_baseline`/`empty_baseline`（全 unconfirmed）
- `collect_directory_entries` 重构：返回 `(dict[vendor_id, list[DirectoryRecord]], errors)`；`DirectoryRecord NamedTuple {id, date: str|None, origin: Literal["openrouter","modelsdev"]}`——openrouter 记 `created`(UTC→日期)、modelsdev 记 `release_date`；同一模型两源各一条记录（id 形态不同不冲突）
- 删 `apply_judgment`/`finalize_state`/`PENDING_FREEZE_DAYS`/`screen_new_entries`/`line_matches`；新增 `model_matches(model, text) -> bool`（match any-of 且不被 exclude 挡，text 已 casefold）与 `discover_unmaintained(vendor, state, records) -> list[str]`（不匹配任何已维护模型且不在 seen 的条目 id）
- 测试：状态 roundtrip、目录采集两源日期归位、占位/未来日期防护函数、model_matches、discover 去重与 seen 过滤

## T3 确认管线

**文件**：新 `src/free_model_spider/core/vendor_confirm.py`、`src/free_model_spider/core/vendor_judge.py`（微调）、`tests/test_vendor_confirm.py`（新）

- `vendor_judge.py` 删除 `classify_entry`/`_classify_user_prompt`/`_lines_block` 及其测试块（spec §8：归类整个消失；`test_vendor_judge.py` 同步裁剪）
- `vendor_judge._article_user_prompt`：`LineConfig` 参数换成模型名上下文（"目标产品线"→"目标模型：{name}"）；`extract_from_article(llm, client, vendor, model_name, url)`——其余（prompt 基座/多格式日期校验/诊断日志）不动
- `vendor_confirm.resolve_model(vendor, model, state, listings, dir_records, llm, client, date) -> ModelState`，按 spec §4：
  1. `model.manual` → confirmed/source=manual/url=manual.url
  2. 厂商有公告源且 `listings` 抓取成功：`model_matches` 命中条目 → 取日期最新 → `extract_from_article` → 过校验 confirmed/source=official；提取失败 → unconfirmed + reason（**不落目录**）；llm None → unconfirmed reason="LLM 未配置"；命中零条目 → 落目录
  3. 公告源抓取失败（listings=None）→ unconfirmed reason="公告源不可用"（暂态，次日重试）
  4. 目录通道：records 里 `model_matches` 命中 → 无 `:` base id 优先 → 多个取日期最新 → 日期过 `valid_directory_date`（≠2024-01-01、2020-01-01≤d≤today）→ confirmed/source=directory/url=None；modelsdev 无效则用 openrouter 记录；全无效 → unconfirmed reason="目录无可用日期"
- 测试：六通道路径逐一（官方成功/提取失败不降级/零命中落目录/占位符拒绝落 openrouter/抓取失败暂态/manual 直取）、confirmed 幂等（resolve 不重入由 cli 保证，这里测 manual 与目录纯函数性）

## T4 报告层

**文件**：`src/free_model_spider/core/vendor_report.py`、`tests/test_vendor_report.py`

- `ModelRow {vendor_name, key, name, url: str|None, corroborating: str|None, release_date, days, source}`；`rows_from_baseline`：仅 confirmed、组内配置序
- md：模型 cell = `[name](官方url)`，url 为 None（目录来源）时链 `corroborating`（⧉ 平台页）；目录来源日期后缀 `†`；图例两行（官方/†目录语义 + ⧉ 说明）
- `render_json`：`models[]` 字段 `{vendor, key, name, url, release_date, source}`（url 可能 null）
- `pending_rows_from_baseline`/`render_pending_markdown`：按模型重构（列：厂商/模型/原因），"次日自动重试"图例保留，删除 7 天冻结表述
- `update_readme_freshness` 同构
- 测试：golden（含 † 行/目录链 ⧉ 行）、pending 明细、JSON 字段序

## T5 CLI 编排

**文件**：`src/free_model_spider/cli.py`、`tests/test_cli_vendors.py`（重写场景）

- `run_vendors` 重写：
  1. 载入基线（vendor-models），无基线 → `empty_baseline`，`first_run=True`
  2. 每厂商：override 概念删除；manual 模型置 confirmed（若基线里已 confirmed 不动，last_verified 重臂）
  3. `fetch_listings`（错误 → 降级日志 + listings=None）；逐 unconfirmed 模型 `resolve_model`，confirmed 跳过（零 LLM）；每模型一行结果日志
  4. 目录发现：`discover_unmaintained` → `first_run` 静默播种，否则 webhook「发现未维护新模型」（每厂商一条、前 3 id）；匹配条目与新条目全部入 seen
  5. 汇总 + 提取失败异常摘要告警（沿用今日机制）+ 报告/-pending/基线落盘/提交（沿用）
- 删 `_bootstrap_vendor`/`_process_new_announcements`/`_freeze_content`/`_announcement_key`/`_attach_corroboration`（⧉ 改为 cli 内联：key→平台页 URL 映射，openrouter 优先）
- 场景测试：①首跑有公告源全确认（每模型恰 1 次 LLM）②无公告源厂商走目录确认（0 LLM）③提取失败暂态：不降级目录、进异常告警、次日（第二次运行）成功后永固 ④占位符日期拒绝 ⑤manual 直取 ⑥发现告警：不匹配新条目 webhook + 入 seen、首跑静默 ⑦confirmed 幂等：次日重跑零 LLM、日期不动 ⑧-pending/报告/提交链路沿用断言

## T6 配置切换与冒烟

**文件**：`data/vendor_releases.yml`（重写）、README 相关段落（如有描述 v2 机制则同步）

- yml：8 家 + spec §9 模型清单（match 按 spec 草案，窄且互斥）；openai/qwen/moonshot 的 announcements 保留探测注释并注明"2026-11-01 复测仍不可爬 → 目录日期通道"
- 本地冒烟（无 LLM key）：`uv run fms vendors --date 2026-11-01 --no-commit` → 预期：降级 3 家目录确认进表（† 标注）、有公告源 5 家 unconfirmed（原因=LLM 未配置）、-pending 生成、rc=0；旧 `data/vendor-releases/` 不再被读
- 全量 `uv run pytest -q` 绿
- 单次提交（精确路径：src 5 文件 + tests 5 文件 + yml + spec/plan 文档）

## 风险与回滚

- 单提交原子切换，回滚 = `git revert` 一次
- 旧 `data/vendor-releases/` 历史保留不删（v2 审计线索）；新基线目录 `vendor-models` 首跑全新开始
- LLM 用量上限天然受控：未确认模型数 × 1 次/天，无 classify、无搜索
