"""v4 状态模型测试：schema 4 基线读写、active/retired 过滤与新鲜度窗口。"""

import json

import pytest

from free_model_spider.core.vendor_state import (
    Baseline,
    LastCheck,
    ModelState,
    VendorState,
    baseline_path,
    days_since,
    find_latest_baseline,
    load_baseline,
    save_baseline,
    window_start,
)


def make_model(key: str, release_date: str, **kwargs: str | None) -> ModelState:
    return ModelState(
        key=key,
        name=key.upper(),
        release_date=release_date,
        url=f"https://example.com/{key}",
        added_on=release_date,
        **kwargs,
    )


def make_baseline(date: str = "2026-11-02") -> Baseline:
    return Baseline(
        date=date,
        vendors=[
            VendorState(
                id="openai",
                models=[
                    make_model("gpt-6-1-terra", "2026-10-26"),
                    make_model("gpt-5-mini", "2026-08-14",
                               retired_on="2026-10-20", superseded_by="gpt-6-1-terra"),
                ],
                last_check=LastCheck(date=date, status="ok", detail=""),
            ),
        ],
    )


def test_roundtrip_writes_schema_alias(tmp_path):
    baseline = make_baseline()
    path = baseline_path(tmp_path, baseline.date)
    save_baseline(baseline, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 4  # 顶层键是 "schema"（by_alias），不是 schema_version
    assert "schema_version" not in data
    loaded = load_baseline(path)
    assert loaded == baseline  # 往返等价
    assert loaded.schema_version == 4


def test_load_rejects_wrong_schema_with_hint(tmp_path):
    path = baseline_path(tmp_path, "2026-10-01")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"date": "2026-10-01", "vendors": []}),  # 无 schema 键，形如 v2 旧文件
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        load_baseline(path)
    message = str(excinfo.value)
    assert "schema" in message
    assert "--bootstrap" in message


def test_baseline_path_uses_vendor_releases_dir(tmp_path):
    assert baseline_path(tmp_path, "2026-11-02") == (
        tmp_path / "vendor-releases" / "2026-11-02.json"
    )


def test_find_latest_baseline_picks_max_date_le_on_or_before(tmp_path):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        save_baseline(make_baseline(day), baseline_path(tmp_path, day))
    latest = find_latest_baseline(tmp_path, "2026-10-01")
    assert latest is not None and latest.stem == "2026-09-30"
    # 同日幂等重跑：stem == on_or_before 也要命中
    same_day = find_latest_baseline(tmp_path, "2026-09-30")
    assert same_day is not None and same_day.stem == "2026-09-30"


def test_find_latest_baseline_ignores_dir_without_json(tmp_path):
    (tmp_path / "vendor-releases").mkdir()
    assert find_latest_baseline(tmp_path, "2026-10-01") is None


def test_find_latest_baseline_none_when_all_after_on_or_before(tmp_path):
    save_baseline(make_baseline("2026-10-02"), baseline_path(tmp_path, "2026-10-02"))
    assert find_latest_baseline(tmp_path, "2026-10-01") is None
    assert find_latest_baseline(tmp_path / "missing", "2026-10-01") is None


def test_days_since_basic():
    assert days_since("2026-10-26", "2026-10-29") == 3
    assert days_since("2026-10-29", "2026-10-29") == 0
    with pytest.raises(ValueError):
        days_since("2026-10-30", "2026-10-29")


def test_active_models_filters_retired():
    state = VendorState(
        id="openai",
        models=[
            make_model("a", "2026-01-01"),
            make_model("b", "2026-02-01", retired_on="2026-03-01", superseded_by="c"),
            make_model("c", "2026-03-01"),
        ],
    )
    assert [m.key for m in state.active_models()] == ["a", "c"]  # 过滤 retired 且保持原序


def test_window_start_uses_max_active_release_date():
    state = VendorState(
        id="openai",
        models=[
            make_model("old", "2026-08-14"),
            make_model("new", "2026-10-01"),
            make_model("fresh", "2026-11-01", retired_on="2026-11-02"),  # retired 不参与
        ],
    )
    assert window_start(state, "2026-11-02") == "2026-10-01"
    # 全部退役与 models 为空都回退 today-180 天
    all_retired = VendorState(
        id="openai",
        models=[make_model("gone", "2026-09-01", retired_on="2026-10-01")],
    )
    assert window_start(all_retired, "2026-11-02") == "2026-05-06"
    assert window_start(VendorState(id="zhipu"), "2026-11-02") == "2026-05-06"
