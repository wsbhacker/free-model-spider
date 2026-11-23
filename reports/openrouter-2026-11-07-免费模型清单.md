# OpenRouter 免费模型清单 2026-11-07
> 免费 15 个（2026-11-06：+1 / -1）

## 🆕 新增 (1)
### liquid/lfm-3-1.7b:free
- 名称：LiquidAI: LFM3-1.7B (free)
- 介绍：LFM3-1.7B 是 Liquid AI 推出的紧凑型文本模型，参数量约 1.7B（Hugging Face 显示约 2B），支持 256K token 超长上下文。该模型经后训练，专为本地智能体、工具调用与结构化输出设计，会先推理再作答，适合领域微调。在长上下文场景下表现突出，8K token 上下文时比上代 LFM2-1.2B 快约 25%。知识密集型任务建议结合检索或搜索使用。
- 要点：1.7B 参数量的紧凑型纯文本模型，上下文长度达 256K token；后训练面向智能体、工具调用与结构化输出，先推理后回答；长上下文性能优化：8K token 时较 LFM2-1.2B 快约 25%；支持领域微调，Hugging Face 月下载量达 66,003；OpenRouter 免费版提示词与输出可能被保留用于训练 Liquid 模型
- 注意：材料未提供完整架构细节与评测基准分数；参数量在元数据与 Hugging Face 页面间略有出入（1.7B/2B）。
- 链接：[平台页](https://openrouter.ai/liquid/lfm-3-1.7b:free) ｜ <https://huggingface.co/LiquidAI/LFM3-1.7B> ｜ <https://docs.liquid.ai/lfm/models/lfm3-1.7b> ｜ <https://www.liquid.ai/blog/lfm3>

## 🗑️ 移除 (1)
- [nvidia/nemotron-3.5-content-safety:free](https://openrouter.ai/nvidia/nemotron-3.5-content-safety:free)（最后出现：2026-11-06）

## 📋 无变化 (14)
| 模型 | 名称 | 上下文 | 模态 |
|---|---|---|---|
| [cohere/north-mini-code:free](https://openrouter.ai/cohere/north-mini-code:free) | Cohere: North Mini Code (free) | 256,000 | text→text |
| [dots-studio/dots-3-note-preview:free](https://openrouter.ai/dots-studio/dots-3-note-preview:free) | Dots Studio: Dots3-Note Preview (free) | 512,000 | text、image→text |
| [google/gemma-4-26b-a4b-it:free](https://openrouter.ai/google/gemma-4-26b-a4b-it:free) | Google: Gemma 4 26B A4B  (free) | 262,144 | image、text、video→text |
| [google/gemma-4-31b-it:free](https://openrouter.ai/google/gemma-4-31b-it:free) | Google: Gemma 4 31B (free) | 262,144 | image、text、video→text |
| [google/lyria-3-clip-preview](https://openrouter.ai/google/lyria-3-clip-preview) | Google: Lyria 3 Clip Preview | 1,048,576 | text、image→text、audio |
| [google/lyria-3-pro-preview](https://openrouter.ai/google/lyria-3-pro-preview) | Google: Lyria 3 Pro Preview | 1,048,576 | text、image→text、audio |
| [liquid/lfm-2.5-2.6b:free](https://openrouter.ai/liquid/lfm-2.5-2.6b:free) | LiquidAI: LFM2.5-2.6B (free) | 65,536 | text→text |
| [nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free](https://openrouter.ai/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free) | NVIDIA: Nemotron 3 Nano Omni (free) | 256,000 | text、audio、image、video→text |
| [nvidia/nemotron-3-ultra-550b-a55b:free](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free) | NVIDIA: Nemotron 3 Ultra (free) | 1,000,000 | text→text |
| [nvidia/nemotron-3.5-lightning:free](https://openrouter.ai/nvidia/nemotron-3.5-lightning:free) | NVIDIA: Nemotron 3.5 Lightning (free) | 1,000,000 | text→text |
| [nvidia/nemotron-3.5-super-vl:free](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free) | NVIDIA: Nemotron 3.5 Super VL (free) | 1,000,000 | text、image、video→text |
| [recursive/flint-v1.0:free](https://openrouter.ai/recursive/flint-v1.0:free) | Recursive: Flint (free) | 262,144 | text→text |
| [stealth/crouton-alpha](https://openrouter.ai/stealth/crouton-alpha) | Crouton Alpha | 1,048,576 | text、image、video、file、audio→text |
| [xingchen-agi/xing4.0-29b-a4b:free](https://openrouter.ai/xingchen-agi/xing4.0-29b-a4b:free) | XingChen: Xing4.0 29B A4B (free) | 262,144 | text→text |

---
快照：data/snapshots/openrouter/2026-11-07.json
