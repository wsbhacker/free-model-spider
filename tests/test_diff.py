from free_model_spider.core.diff import diff_models
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
