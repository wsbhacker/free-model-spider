# free-model-spider

每日抓取各平台免费大模型，与最近快照对比生成「新增 / 移除 / 无变化」三段式
Markdown 日报，由 GitHub Actions 每日北京时间 08:30 自动运行并提交。
每份日报旁附同名 JSON（`schema_version: 1`，含 `added` / `removed` / `unchanged`
三个数组），供其他程序读取。
当前数据源：OpenRouter、OpenCode（免费判定以 opencode.ai/v2/docs/console/models/ 官网定价表为准，
上下文/模态字段经 models.dev 目录补全；架构上支持扩展更多平台，各平台独立追踪、不去重）。

## 日报索引

<!-- fms-index:start -->
### OpenCode

| 日期 | 新增 | 移除 | 日报 |
|---|---|---|---|
| 2026-11-10 | +1 | -1 | [链接](reports/opencode-2026-11-10-免费模型清单.md) |
| 2026-11-09 | +0 | -0 | [链接](reports/opencode-2026-11-09-免费模型清单.md) |
| 2026-11-08 | +0 | -0 | [链接](reports/opencode-2026-11-08-免费模型清单.md) |
| 2026-11-07 | +2 | -1 | [链接](reports/opencode-2026-11-07-免费模型清单.md) |
| 2026-11-06 | +0 | -0 | [链接](reports/opencode-2026-11-06-免费模型清单.md) |
| 2026-11-05 | +1 | -0 | [链接](reports/opencode-2026-11-05-免费模型清单.md) |
| 2026-11-04 | +0 | -0 | [链接](reports/opencode-2026-11-04-免费模型清单.md) |
| 2026-11-03 | +1 | -0 | [链接](reports/opencode-2026-11-03-免费模型清单.md) |
| 2026-11-02 | +0 | -0 | [链接](reports/opencode-2026-11-02-免费模型清单.md) |
| 2026-11-01 | +0 | -0 | [链接](reports/opencode-2026-11-01-免费模型清单.md) |
| 2026-10-31 | +0 | -0 | [链接](reports/opencode-2026-10-31-免费模型清单.md) |
| 2026-10-30 | +0 | -0 | [链接](reports/opencode-2026-10-30-免费模型清单.md) |
| 2026-10-29 | +0 | -0 | [链接](reports/opencode-2026-10-29-免费模型清单.md) |
| 2026-10-28 | +0 | -0 | [链接](reports/opencode-2026-10-28-免费模型清单.md) |
| 2026-10-27 | +0 | -0 | [链接](reports/opencode-2026-10-27-免费模型清单.md) |

### OpenRouter

| 日期 | 新增 | 移除 | 日报 |
|---|---|---|---|
| 2026-11-10 | +1 | -1 | [链接](reports/openrouter-2026-11-10-免费模型清单.md) |
| 2026-11-09 | +0 | -0 | [链接](reports/openrouter-2026-11-09-免费模型清单.md) |
| 2026-11-08 | +0 | -0 | [链接](reports/openrouter-2026-11-08-免费模型清单.md) |
| 2026-11-07 | +1 | -1 | [链接](reports/openrouter-2026-11-07-免费模型清单.md) |
| 2026-11-06 | +0 | -0 | [链接](reports/openrouter-2026-11-06-免费模型清单.md) |
| 2026-11-05 | +0 | -2 | [链接](reports/openrouter-2026-11-05-免费模型清单.md) |
| 2026-11-04 | +1 | -0 | [链接](reports/openrouter-2026-11-04-免费模型清单.md) |
| 2026-11-03 | +0 | -0 | [链接](reports/openrouter-2026-11-03-免费模型清单.md) |
| 2026-11-02 | +0 | -0 | [链接](reports/openrouter-2026-11-02-免费模型清单.md) |
| 2026-11-01 | +0 | -2 | [链接](reports/openrouter-2026-11-01-免费模型清单.md) |
| 2026-10-31 | +0 | -0 | [链接](reports/openrouter-2026-10-31-免费模型清单.md) |
| 2026-10-30 | +0 | -0 | [链接](reports/openrouter-2026-10-30-免费模型清单.md) |
| 2026-10-29 | +0 | -0 | [链接](reports/openrouter-2026-10-29-免费模型清单.md) |
| 2026-10-28 | +0 | -1 | [链接](reports/openrouter-2026-10-28-免费模型清单.md) |
| 2026-10-27 | +0 | -0 | [链接](reports/openrouter-2026-10-27-免费模型清单.md) |
<!-- fms-index:end -->

## 使用

- GitHub Actions：在仓库 Settings → Secrets and variables → Actions 配置
  `LLM_API_KEY`（推荐，OpenRouter 免费 key 即可）与 `BRAVE_API_KEY`（可选）。
  新鲜度日报（厂商发布间隔）由同一定时任务生成；可选配置 Secret
  `ALERT_WEBHOOK_URL`，配置后故障会推送到你的 webhook（body 为 {title, content} JSON）；
  再配 Secret `ALERT_WEBHOOK_TOKEN` 后请求头会附带 `x-token` 供 webhook 侧鉴权。
- 本地运行：

  ```bash
  uv sync
  cp .env.example .env   # 填入 key
  uv run fms run --no-commit
  ```

- 扩展新平台：实现 `Source` 接口（`src/free_model_spider/sources/base.py`）
  并 `@register`，日报与快照自动多出该平台。

<!-- fms-vendors:start -->
### 厂商新鲜度榜

| 厂商 | 模型 | 发布日 | 距今（天） | 证据 |
|---|---|---|---|---|
| OpenAI | GPT-6.1 Terra | 2026-10-26 | 13 | [公告](https://openai.com/index/introducing-gpt-6-1-terra) |
| OpenAI | GPT-6.1 Sol | 2026-09-29 | 40 | [公告](https://openai.com/index/introducing-gpt-6-1-sol) |
| OpenAI | GPT-6 Luna | 2026-09-22 | 47 | [公告](https://openai.com/index/introducing-gpt-6-sol-and-luna) |
| OpenAI | GPT-6 Astra | 2026-09-03 | 66 | [公告](https://openai.com/index/gpt-6-astra) |
| Anthropic | Claude Haiku 5.5 | 2026-10-07 | 32 | [公告](https://www.anthropic.com/claude-haiku-5-5) |
| Anthropic | Claude Sonnet 5.5 | 2026-09-28 | 41 | [公告](https://www.anthropic.com/claude-sonnet-5-5) |
| Anthropic | Claude Opus 5.5 | 2026-09-22 | 47 | [公告](https://www.anthropic.com/claude-opus-5-5) |
| Anthropic | Claude Fable 5.1 | 2026-09-01 | 68 | [公告](https://www.anthropic.com/claude-fable-and-mythos-5-1) |
| Google DeepMind | Gemini 4 Carbon | 2026-10-28 | 11 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-carbon) |
| Google DeepMind | Gemini 4 Argon | 2026-09-30 | 39 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon) |
| Google DeepMind | Gemini 3.8 Flash | 2026-09-02 | 67 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber) |
| DeepSeek | DeepSeek-V4.1-Flash-1017 | 2026-10-17 | 22 | [公告](https://api-docs.deepseek.com/zh-cn/news/news261017) |
| DeepSeek | DeepSeek-V4-Pro-0813 | 2026-08-13 | 87 | [公告](https://api-docs.deepseek.com/zh-cn/news/news260813) |
| 阿里 Qwen | Qwen3.8-Omni-Flash | 2026-09-18 | 51 | [公告](https://qwen.ai/blog?id=qwen3.8-omni-flash) |
| 阿里 Qwen | Qwen3.8-Max | 2026-08-02 | 98 | [公告](https://qwen.ai/blog?id=qwen3.8) |
| 月之暗面 | Kimi K3.1 Pro | 2026-10-24 | 15 | [公告](https://www.kimi.com/news/kimi-k3-1-pro) |
| 月之暗面 | Kimi K3.1 Pro Highspeed | 2026-10-24 | 15 | [公告](https://www.kimi.com/news/kimi-k3-1-pro) |
| 智谱 AI | GLM-5.3-Flash | 2026-08-26 | 74 | [公告](https://docs.bigmodel.cn/cn/update/new-releases) |
| 智谱 AI | GLM-5.3 | 2026-08-19 | 81 | [公告](https://docs.bigmodel.cn/cn/update/new-releases) |
| MiniMax | MiniMax M3.1 Flash | 2026-10-19 | 20 | [公告](https://www.minimax.io/blog/minimax-m31f) |
| MiniMax | MiniMax M3 | 2026-06-01 | 160 | [公告](https://www.minimax.io/blog/minimax-m3) |

> 更新于 2026-11-08
<!-- fms-vendors:end -->
