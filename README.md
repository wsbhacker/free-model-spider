# free-model-spider

每日抓取各平台免费大模型，与最近快照对比生成「新增 / 移除 / 无变化」三段式
Markdown 日报，由 GitHub Actions 每日北京时间 08:30 自动运行并提交。
每份日报旁附同名 JSON（`schema_version: 1`，含 `added` / `removed` / `unchanged`
三个数组），供其他程序读取。
当前数据源：OpenRouter（架构上支持扩展更多平台，各平台独立追踪、不去重）。

## 日报索引

<!-- fms-index:start -->
| 日期 | 平台 | 新增 | 移除 | 日报 |
|---|---|---|---|---|
| 2026-09-30 | OpenRouter | +0 | -0 | [链接](reports/openrouter-2026-09-30-免费模型清单.md) |
<!-- fms-index:end -->

## 使用

- GitHub Actions：在仓库 Settings → Secrets and variables → Actions 配置
  `LLM_API_KEY`（推荐，OpenRouter 免费 key 即可）与 `BRAVE_API_KEY`（可选）。
- 本地运行：

  ```bash
  uv sync
  cp .env.example .env   # 填入 key
  uv run fms run --no-commit
  ```

- 扩展新平台：实现 `Source` 接口（`src/free_model_spider/sources/base.py`）
  并 `@register`，日报与快照自动多出该平台。
