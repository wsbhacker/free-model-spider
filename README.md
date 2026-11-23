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
| 2026-11-23 | +0 | -1 | [链接](reports/opencode-2026-11-23-免费模型清单.md) |
| 2026-11-22 | +1 | -1 | [链接](reports/opencode-2026-11-22-免费模型清单.md) |
| 2026-11-21 | +0 | -0 | [链接](reports/opencode-2026-11-21-免费模型清单.md) |
| 2026-11-20 | +0 | -0 | [链接](reports/opencode-2026-11-20-免费模型清单.md) |
| 2026-11-19 | +0 | -1 | [链接](reports/opencode-2026-11-19-免费模型清单.md) |
| 2026-11-18 | +0 | -2 | [链接](reports/opencode-2026-11-18-免费模型清单.md) |
| 2026-11-17 | +1 | -0 | [链接](reports/opencode-2026-11-17-免费模型清单.md) |
| 2026-11-16 | +0 | -0 | [链接](reports/opencode-2026-11-16-免费模型清单.md) |
| 2026-11-15 | +0 | -0 | [链接](reports/opencode-2026-11-15-免费模型清单.md) |
| 2026-11-14 | +0 | -0 | [链接](reports/opencode-2026-11-14-免费模型清单.md) |
| 2026-11-13 | +0 | -0 | [链接](reports/opencode-2026-11-13-免费模型清单.md) |
| 2026-11-12 | +0 | -0 | [链接](reports/opencode-2026-11-12-免费模型清单.md) |
| 2026-11-11 | +0 | -0 | [链接](reports/opencode-2026-11-11-免费模型清单.md) |
| 2026-11-10 | +2 | -1 | [链接](reports/opencode-2026-11-10-免费模型清单.md) |
| 2026-11-09 | +0 | -0 | [链接](reports/opencode-2026-11-09-免费模型清单.md) |

### OpenRouter

| 日期 | 新增 | 移除 | 日报 |
|---|---|---|---|
| 2026-11-23 | +0 | -0 | [链接](reports/openrouter-2026-11-23-免费模型清单.md) |
| 2026-11-22 | +0 | -0 | [链接](reports/openrouter-2026-11-22-免费模型清单.md) |
| 2026-11-21 | +0 | -1 | [链接](reports/openrouter-2026-11-21-免费模型清单.md) |
| 2026-11-20 | +0 | -2 | [链接](reports/openrouter-2026-11-20-免费模型清单.md) |
| 2026-11-19 | +0 | -0 | [链接](reports/openrouter-2026-11-19-免费模型清单.md) |
| 2026-11-18 | +0 | -0 | [链接](reports/openrouter-2026-11-18-免费模型清单.md) |
| 2026-11-17 | +0 | -0 | [链接](reports/openrouter-2026-11-17-免费模型清单.md) |
| 2026-11-16 | +0 | -0 | [链接](reports/openrouter-2026-11-16-免费模型清单.md) |
| 2026-11-15 | +0 | -0 | [链接](reports/openrouter-2026-11-15-免费模型清单.md) |
| 2026-11-14 | +1 | -2 | [链接](reports/openrouter-2026-11-14-免费模型清单.md) |
| 2026-11-13 | +2 | -0 | [链接](reports/openrouter-2026-11-13-免费模型清单.md) |
| 2026-11-12 | +1 | -0 | [链接](reports/openrouter-2026-11-12-免费模型清单.md) |
| 2026-11-11 | +0 | -1 | [链接](reports/openrouter-2026-11-11-免费模型清单.md) |
| 2026-11-10 | +1 | -1 | [链接](reports/openrouter-2026-11-10-免费模型清单.md) |
| 2026-11-09 | +0 | -0 | [链接](reports/openrouter-2026-11-09-免费模型清单.md) |
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
| OpenAI | GPT-6.2 Astra | 2026-11-17 | 5 | [公告](https://openai.com/index/introducing-gpt-6-2-astra-sol) |
| OpenAI | GPT-6.2 Sol | 2026-11-17 | 5 | [公告](https://openai.com/index/introducing-gpt-6-2-astra-sol) |
| OpenAI | GPT-6 Cyber | 2026-11-05 | 17 | [公告](https://openai.com/index/introducing-gpt-6-cyber) |
| OpenAI | GPT-6.1 Terra | 2026-10-26 | 27 | [公告](https://openai.com/index/introducing-gpt-6-1-terra) |
| OpenAI | GPT-6 Luna | 2026-09-22 | 61 | [公告](https://openai.com/index/introducing-gpt-6-sol-and-luna) |
| Anthropic | Claude Fable 5.2 | 2026-11-09 | 13 | [公告](https://www.anthropic.com/news/claude-fable-and-mythos-5-2) |
| Anthropic | Claude Mythos 5.2 | 2026-11-09 | 13 | [公告](https://www.anthropic.com/news/claude-fable-and-mythos-5-2) |
| Anthropic | Claude Haiku 5.5 | 2026-10-07 | 46 | [公告](https://www.anthropic.com/claude-haiku-5-5) |
| Anthropic | Claude Sonnet 5.5 | 2026-09-28 | 55 | [公告](https://www.anthropic.com/claude-sonnet-5-5) |
| Anthropic | Claude Opus 5.5 | 2026-09-22 | 61 | [公告](https://www.anthropic.com/claude-opus-5-5) |
| Google DeepMind | Gemini 4 Neon | 2026-11-18 | 4 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-neon) |
| Google DeepMind | Gemini 4 Hydrogen | 2026-11-12 | 10 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-hydrogen) |
| Google DeepMind | Gemini 4 Carbon | 2026-10-28 | 25 | [公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-carbon) |
| DeepSeek | DeepSeek-V4.1-Flash-1017 | 2026-10-17 | 36 | [公告](https://api-docs.deepseek.com/zh-cn/news/news261017) |
| DeepSeek | DeepSeek-V4-Pro-0813 | 2026-08-13 | 101 | [公告](https://api-docs.deepseek.com/zh-cn/news/news260813) |
| 阿里 Qwen | Qwen4-Max | 2026-11-16 | 6 | [公告](https://qwen.ai/blog?id=qwen4-max) |
| 阿里 Qwen | Qwen3.8-Omni-Flash | 2026-09-18 | 65 | [公告](https://qwen.ai/blog?id=qwen3.8-omni-flash) |
| 月之暗面 | Kimi K3.1 Pro | 2026-10-24 | 29 | [公告](https://www.kimi.com/news/kimi-k3-1-pro) |
| 月之暗面 | Kimi K3.1 Pro Highspeed | 2026-10-24 | 29 | [公告](https://www.kimi.com/news/kimi-k3-1-pro) |
| 智谱 AI | GLM-5.3-Flash | 2026-08-26 | 88 | [公告](https://docs.bigmodel.cn/cn/update/new-releases) |
| 智谱 AI | GLM-5.3 | 2026-08-19 | 95 | [公告](https://docs.bigmodel.cn/cn/update/new-releases) |
| MiniMax | MiniMax M3.1 Flash | 2026-10-19 | 34 | [公告](https://www.minimax.io/blog/minimax-m31f) |
| MiniMax | MiniMax M3 | 2026-06-01 | 174 | [公告](https://www.minimax.io/blog/minimax-m3) |

> 更新于 2026-11-22
<!-- fms-vendors:end -->
