import json
from pathlib import Path

from free_model_spider.core.vendor_config import VendorConfig, VendorsConfig
from free_model_spider.core.vendor_report import (
    FMS_VENDORS_END,
    FMS_VENDORS_START,
    LEGEND_LINE,
    ModelRow,
    PendingLine,
    RejectedLine,
    render_json,
    render_markdown,
    render_pending_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_state import Baseline, ModelState, VendorState

TODAY = "2026-11-02"


def make_config() -> VendorsConfig:
    return VendorsConfig(
        vendors=[
            VendorConfig(id="anthropic", name="Anthropic",
                         official_domains=["anthropic.com"]),
            VendorConfig(id="zhipu", name="智谱 AI",
                         official_domains=["z.ai"]),
        ],
    )


def make_baseline() -> Baseline:
    """anthropic：两个 active + 一个 retired（v4 生命周期留痕）；zhipu：一个 active。"""
    return Baseline(
        date=TODAY,
        vendors=[
            VendorState(
                id="anthropic",
                models=[
                    ModelState(
                        key="claude-opus-6", name="Claude Opus 6",
                        release_date="2026-11-01",
                        url="https://www.anthropic.com/news/claude-opus-6",
                        added_on=TODAY,
                    ),
                    ModelState(
                        key="claude-sonnet-5-5", name="Claude Sonnet 5.5",
                        release_date="2026-09-20",
                        url="https://www.anthropic.com/news/claude-sonnet-5-5",
                        added_on="2026-09-20",
                    ),
                    ModelState(
                        key="claude-opus-5-5", name="Claude Opus 5.5",
                        release_date="2026-08-14",
                        url="https://www.anthropic.com/news/claude-opus-5-5",
                        added_on="2026-08-14",
                        retired_on=TODAY, superseded_by="claude-opus-6",
                    ),
                ],
            ),
            VendorState(
                id="zhipu",
                models=[
                    ModelState(
                        key="glm-5-3", name="GLM-5.3",
                        release_date="2026-08-18",
                        url="https://docs.z.ai/release-notes/glm-5-3",
                        added_on="2026-08-18",
                    ),
                ],
            ),
        ],
    )


def make_rows() -> list[ModelRow]:
    return rows_from_baseline(make_config(), make_baseline(), TODAY)


def test_rows_active_only_and_sorted_desc():
    # retired 不出现；厂商内按 release_date 倒序；days = days_since(release_date, today)
    assert make_rows() == [
        ModelRow(
            vendor_name="Anthropic", key="claude-opus-6", name="Claude Opus 6",
            url="https://www.anthropic.com/news/claude-opus-6",
            release_date="2026-11-01", days=1,
        ),
        ModelRow(
            vendor_name="Anthropic", key="claude-sonnet-5-5",
            name="Claude Sonnet 5.5",
            url="https://www.anthropic.com/news/claude-sonnet-5-5",
            release_date="2026-09-20", days=43,
        ),
        ModelRow(
            vendor_name="智谱 AI", key="glm-5-3", name="GLM-5.3",
            url="https://docs.z.ai/release-notes/glm-5-3",
            release_date="2026-08-18", days=76,
        ),
    ]


def test_render_markdown_contains_five_columns_and_legend():
    text = render_markdown(TODAY, make_rows())
    assert "| 厂商 | 模型 | 发布日 | 距今（天） | 证据 |" in text
    assert "|---|---|---|---|---|" in text
    assert ("| Anthropic | Claude Opus 6 | 2026-11-01 | 1 | "
            "[公告](https://www.anthropic.com/news/claude-opus-6) |") in text
    assert LEGEND_LINE == (
        "> 发布日与证据均来自厂商官方公告（agent 检索，域名白名单校验）")
    assert LEGEND_LINE in text
    assert ("基线快照：data/vendor-releases/2026-11-02.json · "
            "口径：官方公告日（Asia/Shanghai）") in text
    # 无 url 的行：证据列输出 —
    no_url = render_markdown(
        TODAY, [ModelRow("OpenAI", "gpt-6-1", "GPT-6.1", None, "2026-10-26", 7)])
    assert "| OpenAI | GPT-6.1 | 2026-10-26 | 7 | — |" in no_url


def test_render_json_schema4():
    payload = json.loads(render_json(TODAY, make_rows()))
    assert payload["schema_version"] == 4
    assert payload["date"] == TODAY
    assert payload["models"] == [
        {
            "vendor": "Anthropic", "key": "claude-opus-6", "name": "Claude Opus 6",
            "url": "https://www.anthropic.com/news/claude-opus-6",
            "release_date": "2026-11-01", "days": 1,
        },
        {
            "vendor": "Anthropic", "key": "claude-sonnet-5-5",
            "name": "Claude Sonnet 5.5",
            "url": "https://www.anthropic.com/news/claude-sonnet-5-5",
            "release_date": "2026-09-20", "days": 43,
        },
        {
            "vendor": "智谱 AI", "key": "glm-5-3", "name": "GLM-5.3",
            "url": "https://docs.z.ai/release-notes/glm-5-3",
            "release_date": "2026-08-18", "days": 76,
        },
    ]
    # url 可为 null（手动维护的草稿条目可能缺证据链接）
    empty_url = json.loads(
        render_json(TODAY, [ModelRow("OpenAI", "gpt-6-1", "GPT-6.1", None,
                                     "2026-10-26", 7)]))
    assert empty_url["models"][0]["url"] is None


# ---------- -pending 未决明细（三节，空节省略，拒收含原文摘录前 500 字） ----------


def test_render_pending_three_sections_and_omits_empty():
    rejected = [
        RejectedLine("Anthropic", "Claude Fake 9",
                     "证据域名非官方：https://evil.example.com/x",
                     "原文" * 400),  # 800 字 > 500
    ]
    text = render_pending_markdown(
        TODAY,
        failed=[PendingLine(
            "月之暗面",
            "未建基线，请运行 fms vendors --bootstrap --vendor moonshot")],
        transient=[],
        rejected=rejected,
    )
    assert text.startswith(f"# 各大厂商大模型新鲜度 {TODAY} 未决明细")
    assert "## 未建基线 / 处理异常" in text
    assert "月之暗面：未建基线" in text
    assert "## 拒收条目" in text
    assert "Anthropic / Claude Fake 9：证据域名非官方" in text
    assert "## 暂态失败" not in text  # 空节省略
    excerpt_line = next(line for line in text.splitlines()
                        if line.strip().startswith(">") and "原文" in line)
    assert "原文" * 250 in excerpt_line  # 恰好前 500 字
    assert "原文" * 251 not in excerpt_line


def test_render_pending_transient_section():
    text = render_pending_markdown(
        TODAY,
        failed=[],
        transient=[PendingLine("Anthropic", "Dify HTTP 503（暂态，下次运行重试）")],
        rejected=[],
    )
    assert "## 暂态失败（下次运行重试）" in text
    assert "Anthropic：Dify HTTP 503" in text
    assert "## 未建基线" not in text and "## 拒收条目" not in text


# ---------- README 常驻区块（update_readme_freshness 逻辑沿用 v3） ----------


def test_update_readme_freshness_rewrite_idempotent(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n", encoding="utf-8")
    update_readme_freshness(readme, TODAY, make_rows())
    first = readme.read_text("utf-8")
    assert FMS_VENDORS_START in first and FMS_VENDORS_END in first
    assert "| 厂商 | 模型 | 发布日 | 距今（天） | 证据 |" in first

    # 次日只剩一行：重写后旧行不得残留，逐字节幂等
    slim_rows = make_rows()[:1]
    update_readme_freshness(readme, "2026-11-03", slim_rows)
    second = readme.read_text("utf-8")
    assert "GLM-5.3" not in second
    assert "Claude Sonnet 5.5" not in second
    assert "Claude Opus 6" in second
    assert "> 更新于 2026-11-03" in second
    update_readme_freshness(readme, "2026-11-03", slim_rows)
    assert readme.read_text("utf-8") == second
