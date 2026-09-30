import json

from free_model_spider.core.diff import DiffResult, diff_models, dump_diff_json
from free_model_spider.sources.base import ModelRecord


def rec(id_: str) -> ModelRecord:
    return ModelRecord(source="openrouter", id=id_, name=id_)


def test_added_removed_unchanged_sorted():
    today = [rec("c/z"), rec("a/x"), rec("b/y")]
    yesterday = [rec("a/x"), rec("d/w")]
    result = diff_models(today, yesterday)
    assert [r.id for r in result.added] == ["b/y", "c/z"]
    assert [r.id for r in result.removed] == ["d/w"]
    assert [r.id for r in result.unchanged] == ["a/x"]


def test_first_day_baseline():
    result = diff_models([rec("a/x")], [])
    assert [r.id for r in result.added] == ["a/x"]
    assert result.removed == [] and result.unchanged == []


def test_dump_diff_json_shape():
    added = ModelRecord(
        source="openrouter", id="b/y", name="Y", context_length=2000,
        input_modalities=["text"], output_modalities=["text"],
        links={"platform_page": "https://e.com/b"},
        raw={"architecture": {"x": 1}},
    )
    diff = DiffResult(added=[added], removed=[], unchanged=[rec("a/x")])
    text = dump_diff_json(
        diff, source="openrouter", display_name="OpenRouter",
        date="2026-10-01", baseline_date="2026-09-30", total=2,
    )
    data = json.loads(text)
    assert data["schema_version"] == 1
    assert data["source"] == "openrouter"
    assert data["display_name"] == "OpenRouter"
    assert data["date"] == "2026-10-01"
    assert data["baseline_date"] == "2026-09-30"
    assert data["total"] == 2
    assert [m["id"] for m in data["added"]] == ["b/y"]
    assert [m["id"] for m in data["unchanged"]] == ["a/x"]
    assert data["removed"] == []
    slim = data["added"][0]
    assert slim["name"] == "Y"
    assert slim["context_length"] == 2000
    assert slim["links"]["platform_page"] == "https://e.com/b"
    assert slim["page_url"] == "https://e.com/b"
    assert "raw" not in slim


def test_dump_diff_json_baseline_sorted():
    diff = DiffResult(added=[], removed=[], unchanged=[rec("b/y"), rec("a/x")])
    text = dump_diff_json(
        diff, source="openrouter", display_name="OpenRouter",
        date="2026-09-30", baseline_date=None, total=2,
    )
    data = json.loads(text)
    assert data["baseline_date"] is None
    assert data["added"] == []
    assert data["removed"] == []
    assert [m["id"] for m in data["unchanged"]] == ["a/x", "b/y"]
