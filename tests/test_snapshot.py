from free_model_spider.core.snapshot import (
    find_latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from free_model_spider.sources.base import ModelRecord


def make_records():
    return [
        ModelRecord(source="openrouter", id="a/x:free", name="X"),
        ModelRecord(source="openrouter", id="b/y:free", name="Y"),
    ]


def test_save_and_load_roundtrip(tmp_path):
    path = snapshot_path(tmp_path, "openrouter", "2026-09-30")
    save_snapshot(make_records(), path, "openrouter", "2026-09-30")
    loaded = load_snapshot(path)
    assert [r.id for r in loaded] == ["a/x:free", "b/y:free"]


def test_save_sorts_by_id(tmp_path):
    path = snapshot_path(tmp_path, "openrouter", "2026-09-30")
    save_snapshot(list(reversed(make_records())), path, "openrouter", "2026-09-30")
    text = path.read_text(encoding="utf-8")
    assert text.index("a/x:free") < text.index("b/y:free")


def test_find_latest_snapshot(tmp_path):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        save_snapshot(make_records(), snapshot_path(tmp_path, "openrouter", day),
                      "openrouter", day)
    latest = find_latest_snapshot(tmp_path, "openrouter", "2026-10-01")
    assert latest is not None and latest.stem == "2026-09-30"
    assert find_latest_snapshot(tmp_path, "openrouter", "2026-09-28") is None
    assert find_latest_snapshot(tmp_path, "other", "2026-10-01") is None
