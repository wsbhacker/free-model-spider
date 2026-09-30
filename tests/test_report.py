from pathlib import Path

from free_model_spider.core.diff import diff_models
from free_model_spider.core.intro import metadata_card
from free_model_spider.core.report import render_report, update_readme_index
from free_model_spider.sources.base import ModelRecord


def rec(id_: str, name: str = "", ctx: int | None = 262144) -> ModelRecord:
    return ModelRecord(
        source="openrouter", id=id_, name=name or id_, context_length=ctx,
        input_modalities=["text"], output_modalities=["text"],
        links={"platform_page": f"https://openrouter.ai/{id_}"},
    )


def build(added_ids, removed_ids, unchanged_ids):
    today = [rec(i) for i in added_ids + unchanged_ids]
    yesterday = [rec(i) for i in removed_ids + unchanged_ids]
    diff = diff_models(today, yesterday)
    intros = {r.id: metadata_card(r) for r in diff.added}
    return today, yesterday, diff, intros


def test_change_day_report():
    today, _, diff, intros = build(["a/new:free"], ["z/old:free"], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "data/snapshots/openrouter/2026-09-30.json")
    assert text.startswith("# OpenRouter 免费模型清单 2026-09-30")
    assert "免费 2 个（2026-09-29：+1 / -1）" in text
    assert "## 🆕 新增 (1)" in text
    assert "### a/new:free" in text
    assert "https://openrouter.ai/a/new:free" in text
    assert "## 🗑️ 移除 (1)" in text
    assert "[z/old:free](https://openrouter.ai/z/old:free)（最后出现：2026-09-29）" in text
    assert "## 📋 无变化 (1)" in text
    assert "[m/mid:free](https://openrouter.ai/m/mid:free)" in text
    assert text.rstrip().endswith("data/snapshots/openrouter/2026-09-30.json")


def test_no_change_day_marks_sections():
    today, _, diff, intros = build([], [], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json")
    assert "## 🆕 新增——无变化" in text
    assert "## 🗑️ 移除——无变化" in text
    assert "免费 1 个（2026-09-29：+0 / -0）" in text


def test_first_day_same_format_as_no_change():
    today = [rec("m/mid:free")]
    diff = diff_models(today, [])
    intros = {r.id: metadata_card(r) for r in diff.added}
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         None, "snap.json")
    assert "## 🆕 新增——无变化" in text
    assert "## 🗑️ 移除——无变化" in text
    assert "## 📋 无变化 (1)" in text


def test_sections_sorted_alphabetically():
    today, _, diff, intros = build(["c/z:free", "a/x:free"], [], [])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json")
    assert text.index("### a/x:free") < text.index("### c/z:free")


def test_anomalies_section():
    today, _, diff, intros = build([], [], ["m/mid:free"])
    text = render_report("OpenRouter", "2026-09-30", today, diff, intros,
                         "2026-09-29", "snap.json",
                         anomalies=["<未知id>: KeyError('id')"])
    assert "## ⚠️ 异常 (1)" in text
    assert "- <未知id>: KeyError('id')" in text


def test_update_readme_index(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# free-model-spider\n\n## 日报索引\n\n<!-- fms-index:start -->\n"
                      "| 日期 | 平台 | 新增 | 移除 | 日报 |\n|---|---|---|---|---|\n"
                      "<!-- fms-index:end -->\n", encoding="utf-8")
    update_readme_index(readme, [
        ("2026-09-30", "openrouter", "OpenRouter", 2, 1,
         "reports/openrouter-2026-09-30-免费模型清单.md"),
        ("2026-09-29", "openrouter", "OpenRouter", 0, 0,
         "reports/openrouter-2026-09-29-免费模型清单.md"),
    ])
    text = readme.read_text(encoding="utf-8")
    assert "reports/openrouter-2026-09-30-免费模型清单.md" in text
    assert text.index("2026-09-30") < text.index("2026-09-29")  # 日期倒序

    # 同日重跑：替换旧行而不是追加
    update_readme_index(readme, [
        ("2026-09-30", "openrouter", "OpenRouter", 3, 0,
         "reports/openrouter-2026-09-30-免费模型清单.md"),
    ])
    text = readme.read_text(encoding="utf-8")
    assert text.count("| 2026-09-30 | OpenRouter |") == 1
    assert "+3" in text and "+2" not in text
