from pathlib import Path

from free_model_spider.core.vendor_config import (
    ModelConfig,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.core.vendor_report import (
    FMS_VENDORS_END,
    FMS_VENDORS_START,
    ModelRow,
    PendingRow,
    pending_rows_from_baseline,
    render_json,
    render_markdown,
    render_pending_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_stats import Baseline, ModelState, VendorState


def make_config() -> VendorReleasesConfig:
    return VendorReleasesConfig(
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"]},
                models=[
                    ModelConfig(key="gpt-6-1-terra", name="GPT-6.1 Terra",
                                match=[r"gpt[ -]?6\.1[ -]?terra"]),
                    ModelConfig(key="gpt-6-1-mini", name="GPT-6.1 mini",
                                match=[r"gpt[ -]?6\.1[ -]?mini"]),
                ],
            ),
            VendorConfig(
                id="zhipu",
                name="智谱 AI",
                official_domains=["z.ai"],
                directory_slugs={"openrouter": ["z-ai"]},
                models=[
                    ModelConfig(key="glm-5-3", name="GLM-5.3",
                                match=[r"glm[ -]?5\.3"], exclude=["flash"]),
                    ModelConfig(key="glm-5-3-flash", name="GLM-5.3 Flash",
                                match=[r"glm[ -]?5\.3[ -]?flash"]),
                ],
            ),
        ],
    )


def make_baseline() -> Baseline:
    return Baseline(
        date="2026-11-01",
        vendors=[
            VendorState(
                id="openai",
                models=[
                    ModelState(
                        key="gpt-6-1-terra", name="GPT-6.1 Terra",
                        date="2026-10-26", url=None, source="directory",
                        status="confirmed", last_verified="2026-11-01",
                    ),
                    ModelState(
                        key="gpt-6-1-mini", name="GPT-6.1 mini",
                        status="unconfirmed", reason="LLM 未配置（暂态，次日重试）",
                    ),
                ],
            ),
            VendorState(
                id="zhipu",
                models=[
                    ModelState(
                        key="glm-5-3", name="GLM-5.3",
                        date="2026-08-18", url="https://docs.z.ai/release-notes/glm-5-3",
                        source="official", status="confirmed",
                        last_verified="2026-11-01",
                    ),
                    ModelState(
                        key="glm-5-3-flash", name="GLM-5.3 Flash",
                        status="unconfirmed", reason="公告提取/校验失败（暂态，次日重试）",
                    ),
                ],
            ),
        ],
    )


CORROBORATIONS = {
    "gpt-6-1-terra": "https://openrouter.ai/openai/gpt-6.1-terra",
    "glm-5-3": "https://openrouter.ai/z-ai/glm-5.3",
}


def make_rows() -> list[ModelRow]:
    return rows_from_baseline(make_config(), make_baseline(), "2026-11-01",
                              CORROBORATIONS)


def test_rows_from_baseline_orders_and_filters():
    # 配置厂商/模型顺序；unconfirmed 不进表；目录来源 url=None、佐证回填 ⧉
    assert make_rows() == [
        ModelRow(
            vendor_name="OpenAI", key="gpt-6-1-terra", name="GPT-6.1 Terra",
            url=None, corroborating="https://openrouter.ai/openai/gpt-6.1-terra",
            release_date="2026-10-26", days=6, source="directory",
        ),
        ModelRow(
            vendor_name="智谱 AI", key="glm-5-3", name="GLM-5.3",
            url="https://docs.z.ai/release-notes/glm-5-3",
            corroborating="https://openrouter.ai/z-ai/glm-5.3",
            release_date="2026-08-18", days=75, source="official",
        ),
    ]


EXPECTED_MARKDOWN = """# 各大厂商大模型新鲜度 2026-11-01

## 🥬 各大厂商大模型新鲜度

| 厂商 | 模型 | 发布日 | 天数 |
|---|---|---|---|
| OpenAI | [GPT-6.1 Terra](https://openrouter.ai/openai/gpt-6.1-terra) | 2026-10-26† | 6 |
| 智谱 AI | [GLM-5.3](https://docs.z.ai/release-notes/glm-5-3) [⧉](https://openrouter.ai/z-ai/glm-5.3) | 2026-08-18 | 75 |

> 模型名链接 = 官方公告页（official，域名白名单校验）或 ⧉ 目录平台页（directory 来源）；`†` = 目录日期（models.dev 策展 / OpenRouter 上架），可能滞后官方公告数日

---
基线快照：data/vendor-models/2026-11-01.json · 口径：官方公告日（Asia/Shanghai）
"""


def test_render_markdown_golden():
    # 目录来源：无官方 URL → 模型名链 ⧉ 平台页、日期带 †；官方来源：官方链接 + ⧉ 佐证
    assert render_markdown("2026-11-01", make_rows()) == EXPECTED_MARKDOWN


def test_render_json_golden():
    expected = """{
  "schema_version": 1,
  "date": "2026-11-01",
  "models": [
    {
      "vendor": "OpenAI",
      "key": "gpt-6-1-terra",
      "name": "GPT-6.1 Terra",
      "url": null,
      "release_date": "2026-10-26",
      "source": "directory"
    },
    {
      "vendor": "智谱 AI",
      "key": "glm-5-3",
      "name": "GLM-5.3",
      "url": "https://docs.z.ai/release-notes/glm-5-3",
      "release_date": "2026-08-18",
      "source": "official"
    }
  ]
}
"""
    assert render_json("2026-11-01", make_rows()) == expected


# ---------- -pending 未决明细报告 ----------


def test_pending_rows_from_baseline_collects_unconfirmed_models():
    assert pending_rows_from_baseline(make_config(), make_baseline()) == [
        PendingRow("OpenAI", "GPT-6.1 mini", "LLM 未配置（暂态，次日重试）"),
        PendingRow("智谱 AI", "GLM-5.3 Flash", "公告提取/校验失败（暂态，次日重试）"),
    ]


def test_render_pending_markdown_lists_rows_and_failed_vendors():
    text = render_pending_markdown(
        "2026-11-01",
        [PendingRow("OpenAI", "GPT-6.1 mini", "LLM 未配置（暂态，次日重试）")],
        failed_vendors=["qwen"],
    )
    assert text.startswith("# 各大厂商大模型新鲜度 2026-11-01 未决明细")
    assert "| OpenAI | GPT-6.1 mini | LLM 未配置（暂态，次日重试） |" in text
    assert "处理失败厂商（当日整体跳过）：qwen" in text
    assert "次日自动重试" in text


def test_render_pending_markdown_without_failed_vendors_omits_footer():
    assert "处理失败厂商" not in render_pending_markdown("2026-11-01", [])


# ---------- README 常驻区块 ----------


def test_update_readme_freshness_appends_block(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n\n## 日报索引\n\n已有内容\n", encoding="utf-8")
    update_readme_freshness(readme, "2026-11-01", make_rows())
    text = readme.read_text(encoding="utf-8")
    start = text.index(FMS_VENDORS_START)
    end = text.index(FMS_VENDORS_END)
    assert start > text.index("已有内容"), "区块追加在原有内容之后"
    assert text.endswith(FMS_VENDORS_END + "\n")
    block = text[start + len(FMS_VENDORS_START):end]
    assert block.startswith("\n### 厂商新鲜度榜\n\n| 厂商 | 模型 | 发布日 | 天数 |")
    assert "2026-10-26† | 6 |" in block  # † 标注随表进 README
    assert block.endswith("\n\n> 更新于 2026-11-01\n")


def test_update_readme_freshness_rewrite_idempotent(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n", encoding="utf-8")
    update_readme_freshness(readme, "2026-11-01", make_rows())
    first = readme.read_text(encoding="utf-8")

    # 次日只剩一行：重写后旧行不得残留，逐字节幂等
    slim_rows = make_rows()[:1]
    update_readme_freshness(readme, "2026-11-02", slim_rows)
    second = readme.read_text(encoding="utf-8")
    assert "GLM-5.3" not in second
    assert "> 更新于 2026-11-02" in second
    update_readme_freshness(readme, "2026-11-02", slim_rows)
    assert readme.read_text(encoding="utf-8") == second
