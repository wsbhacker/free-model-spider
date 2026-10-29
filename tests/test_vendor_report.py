from pathlib import Path

from free_model_spider.core.vendor_config import (
    LineConfig,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.core.vendor_report import (
    FMS_VENDORS_END,
    FMS_VENDORS_START,
    VendorRow,
    render_json,
    render_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_stats import Baseline, LineState, VendorState


def make_config() -> VendorReleasesConfig:
    return VendorReleasesConfig(
        stale_days=45,
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"]},
                lines=[
                    LineConfig(key="flagship", name="GPT 主线", match=[r"gpt-\d"]),
                    LineConfig(key="sub", name="GPT mini", match=["mini"]),
                ],
            ),
            VendorConfig(
                id="anthropic",
                name="Anthropic",
                official_domains=["anthropic.com"],
                directory_slugs={"openrouter": ["anthropic"]},
                lines=[
                    LineConfig(key="flagship", name="Claude Opus 主线", match=[r"opus"]),
                    LineConfig(key="sub", name="Claude Sonnet 主线", match=[r"sonnet"]),
                ],
            ),
            VendorConfig(
                id="zhipu",
                name="智谱 AI",
                official_domains=["z.ai"],
                directory_slugs={"openrouter": ["z-ai"], "modelsdev": ["z-ai"]},
                lines=[
                    LineConfig(key="flagship", name="GLM 主线", match=[r"glm-\d"]),
                    LineConfig(key="sub", name="GLM FlashX", match=["flashx"]),
                ],
            ),
        ],
    )


def make_baseline() -> Baseline:
    return Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-6.1-terra"],
                lines=[
                    LineState(
                        key="flagship",
                        model="GPT-6.1 Terra",
                        release_date="2026-10-26",
                        url="https://openai.com/gpt-6-1",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                    LineState(
                        key="sub",
                        model="GPT-6.1 mini",
                        release_date="2026-10-26",
                        url="https://openai.com/gpt-6-1-mini",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                ],
            ),
            VendorState(
                id="anthropic",
                lines=[
                    LineState(
                        key="flagship",
                        model="Claude Opus 5.5",
                        release_date="2026-09-15",
                        url="https://anthropic.com/opus-5-5",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                    LineState(
                        key="sub",
                        model="Claude Sonnet 5.5",
                        release_date="2026-09-29",
                        url="https://anthropic.com/sonnet-5-5",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                ],
            ),
            VendorState(
                id="zhipu",
                lines=[
                    LineState(
                        key="flagship",
                        model="GLM-5.3",
                        release_date="2026-09-24",
                        url="https://z.ai/glm-5-3",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                    LineState(key="sub", status="pending", reason="裁决缺失", pending_days=2),
                ],
            ),
        ],
    )


CORROBORATIONS = {
    "GPT-6.1 Terra": "https://openrouter.ai/openai/gpt-6.1-terra",
    "Claude Opus 5.5": "https://openrouter.ai/anthropic/claude-opus-5.5",
    "GLM-5.3": "https://openrouter.ai/z-ai/glm-5.3",
}


def make_rows() -> list[VendorRow]:
    return rows_from_baseline(make_config(), make_baseline(), "2026-10-29", CORROBORATIONS)


def test_rows_from_baseline_orders_and_filters():
    # 配置厂商顺序、组内 flagship 在前；zhipu/sub pending 未确认被过滤；
    # 佐证按 model 名查 corroborations，缺失为 None；天数 = today - release_date。
    assert make_rows() == [
        VendorRow(
            vendor_name="OpenAI",
            model="GPT-6.1 Terra",
            url="https://openai.com/gpt-6-1",
            corroborating="https://openrouter.ai/openai/gpt-6.1-terra",
            release_date="2026-10-26",
            days=3,
        ),
        VendorRow(
            vendor_name="OpenAI",
            model="GPT-6.1 mini",
            url="https://openai.com/gpt-6-1-mini",
            corroborating=None,
            release_date="2026-10-26",
            days=3,
        ),
        VendorRow(
            vendor_name="Anthropic",
            model="Claude Opus 5.5",
            url="https://anthropic.com/opus-5-5",
            corroborating="https://openrouter.ai/anthropic/claude-opus-5.5",
            release_date="2026-09-15",
            days=44,
        ),
        VendorRow(
            vendor_name="Anthropic",
            model="Claude Sonnet 5.5",
            url="https://anthropic.com/sonnet-5-5",
            corroborating=None,
            release_date="2026-09-29",
            days=30,
        ),
        VendorRow(
            vendor_name="智谱 AI",
            model="GLM-5.3",
            url="https://z.ai/glm-5-3",
            corroborating="https://openrouter.ai/z-ai/glm-5.3",
            release_date="2026-09-24",
            days=35,
        ),
    ]


EXPECTED_MARKDOWN = """# 各大厂商大模型新鲜度 2026-10-29

## 🥬 各大厂商大模型新鲜度

| 厂商 | 模型 | 发布日 | 天数 |
|---|---|---|---|
| OpenAI | [GPT-6.1 Terra](https://openai.com/gpt-6-1) [⧉](https://openrouter.ai/openai/gpt-6.1-terra) | 2026-10-26 | 3 |
|  | [GPT-6.1 mini](https://openai.com/gpt-6-1-mini) | 2026-10-26 | 3 |
| Anthropic | [Claude Opus 5.5](https://anthropic.com/opus-5-5) [⧉](https://openrouter.ai/anthropic/claude-opus-5.5) | 2026-09-15 | 44 |
|  | [Claude Sonnet 5.5](https://anthropic.com/sonnet-5-5) | 2026-09-29 | 30 |
| 智谱 AI | [GLM-5.3](https://z.ai/glm-5-3) [⧉](https://openrouter.ai/z-ai/glm-5.3) | 2026-09-24 | 35 |

> 模型名链接 = 官方公告/产品页（进表硬条件，域名经白名单校验）；`⧉` = 第三方目录佐证（OpenRouter / models.dev 平台页）

---
基线快照：data/vendor-releases/2026-10-29.json · 口径：官方公告日（Asia/Shanghai）
"""


def test_render_markdown_golden():
    assert render_markdown("2026-10-29", make_rows()) == EXPECTED_MARKDOWN


EXPECTED_JSON = """{
  "schema_version": 1,
  "date": "2026-10-29",
  "models": [
    {
      "vendor": "OpenAI",
      "name": "GPT-6.1 Terra",
      "url": "https://openai.com/gpt-6-1",
      "release_date": "2026-10-26"
    },
    {
      "vendor": "OpenAI",
      "name": "GPT-6.1 mini",
      "url": "https://openai.com/gpt-6-1-mini",
      "release_date": "2026-10-26"
    },
    {
      "vendor": "Anthropic",
      "name": "Claude Opus 5.5",
      "url": "https://anthropic.com/opus-5-5",
      "release_date": "2026-09-15"
    },
    {
      "vendor": "Anthropic",
      "name": "Claude Sonnet 5.5",
      "url": "https://anthropic.com/sonnet-5-5",
      "release_date": "2026-09-29"
    },
    {
      "vendor": "智谱 AI",
      "name": "GLM-5.3",
      "url": "https://z.ai/glm-5-3",
      "release_date": "2026-09-24"
    }
  ]
}
"""


def test_render_json_golden():
    assert render_json("2026-10-29", make_rows()) == EXPECTED_JSON


def test_render_json_empty_rows():
    assert render_json(
        "2026-10-29", []
    ) == '{\n  "schema_version": 1,\n  "date": "2026-10-29",\n  "models": []\n}\n'


def test_update_readme_freshness_appends_block(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n\n## 日报索引\n\n已有内容\n", encoding="utf-8")
    update_readme_freshness(readme, "2026-10-29", make_rows())
    text = readme.read_text(encoding="utf-8")
    start = text.index(FMS_VENDORS_START)
    end = text.index(FMS_VENDORS_END)
    assert start > text.index("已有内容"), "区块追加在原有内容之后"
    assert text.endswith(FMS_VENDORS_END + "\n")
    block = text[start + len(FMS_VENDORS_START) : end]
    assert block.startswith("\n### 厂商新鲜度榜\n\n| 厂商 | 模型 | 发布日 | 天数 |")
    assert (
        "| OpenAI | [GPT-6.1 Terra](https://openai.com/gpt-6-1) "
        "[⧉](https://openrouter.ai/openai/gpt-6.1-terra) | 2026-10-26 | 3 |" in block
    )
    assert "|  | [GPT-6.1 mini](https://openai.com/gpt-6-1-mini) | 2026-10-26 | 3 |" in block
    assert block.endswith("\n\n> 更新于 2026-10-29\n")


def test_update_readme_freshness_rewrite_idempotent(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n", encoding="utf-8")
    update_readme_freshness(readme, "2026-10-29", make_rows())
    first = readme.read_text(encoding="utf-8")

    # 次日只剩 OpenAI 两行：重写后旧厂商行（智谱 GLM-5.3 等）不得残留
    slim = Baseline(date="2026-10-30", vendors=[make_baseline().vendors[0]])
    slim_rows = rows_from_baseline(make_config(), slim, "2026-10-30", CORROBORATIONS)
    update_readme_freshness(readme, "2026-10-30", slim_rows)
    second = readme.read_text(encoding="utf-8")
    assert second.startswith("# free-model-spider\n")
    assert second.count(FMS_VENDORS_START) == 1
    assert second.count(FMS_VENDORS_END) == 1
    assert "GLM-5.3" not in second
    assert "Claude Opus 5.5" not in second
    # days 随 today 重算：2026-10-30 - 2026-10-26 = 4
    assert "| OpenAI | [GPT-6.1 Terra](https://openai.com/gpt-6-1) [⧉](https://openrouter.ai/openai/gpt-6.1-terra) | 2026-10-26 | 4 |" in second
    assert "> 更新于 2026-10-30" in second
    assert "> 更新于 2026-10-29" not in second

    # 同数据重跑：逐字节幂等
    update_readme_freshness(readme, "2026-10-30", slim_rows)
    assert readme.read_text(encoding="utf-8") == second
