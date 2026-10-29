from pathlib import Path

import pytest
from pydantic import ValidationError

from free_model_spider.core.vendor_config import (
    LineConfig,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.core.vendor_stats import (
    Baseline,
    LineState,
    VendorState,
    baseline_path,
    days_since,
    empty_baseline,
    find_latest_baseline,
    load_baseline,
    save_baseline,
)


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
                id="meta",
                name="Meta",
                official_domains=["ai.meta.com"],
                directory_slugs={"openrouter": ["meta-llama"]},
                lines=[LineConfig(key="flagship", name="Llama 主线", match=[r"llama-\d"])],
            ),
        ],
    )


def make_baseline() -> Baseline:
    return Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                seen_directory_ids=["gpt-5.1", "gpt-5.1-mini"],
                lines=[
                    LineState(
                        key="flagship",
                        model="gpt-5.1",
                        release_date="2026-10-26",
                        url="https://openai.com/blog/gpt-5-1",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                    LineState(
                        key="sub",
                        status="stale-baseline",
                        reason="45 天未更新",
                        pending_days=60,
                    ),
                ],
            ),
            VendorState(
                id="meta",
                lines=[LineState(key="flagship", status="pending")],
            ),
        ],
    )


def test_baseline_path(tmp_path):
    assert baseline_path(tmp_path, "2026-10-29") == (
        tmp_path / "vendor-releases" / "2026-10-29.json"
    )


def test_save_and_load_roundtrip_preserves_structure(tmp_path):
    baseline = make_baseline()
    path = baseline_path(tmp_path, baseline.date)
    save_baseline(baseline, path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "45 天未更新" in text  # ensure_ascii=False
    assert "{\n  \"date\": \"2026-10-29\"" in text  # indent=2
    loaded = load_baseline(path)
    assert loaded == baseline
    assert loaded.date == "2026-10-29"
    assert [v.id for v in loaded.vendors] == ["openai", "meta"]
    openai = loaded.vendors[0]
    assert openai.seen_directory_ids == ["gpt-5.1", "gpt-5.1-mini"]
    assert [line.key for line in openai.lines] == ["flagship", "sub"]
    assert openai.lines[0].model == "gpt-5.1"
    assert openai.lines[0].release_date == "2026-10-26"
    assert openai.lines[0].status == "ok"
    assert openai.lines[1].status == "stale-baseline"
    assert openai.lines[1].reason == "45 天未更新"
    assert openai.lines[1].pending_days == 60
    assert loaded.vendors[1].lines[0].status == "pending"


def test_find_latest_baseline_picks_max_date_le_on_or_before(tmp_path):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        save_baseline(make_baseline(), baseline_path(tmp_path, day))
    latest = find_latest_baseline(tmp_path, "2026-10-01")
    assert latest is not None and latest.stem == "2026-09-30"
    # 同日幂等重跑：stem == on_or_before 也要命中（与 snapshot 的 < 不同）
    same_day = find_latest_baseline(tmp_path, "2026-09-30")
    assert same_day is not None and same_day.stem == "2026-09-30"


def test_find_latest_baseline_ignores_dir_without_json(tmp_path):
    (tmp_path / "vendor-releases").mkdir()
    assert find_latest_baseline(tmp_path, "2026-10-01") is None


def test_find_latest_baseline_none_when_all_after_on_or_before(tmp_path):
    save_baseline(make_baseline(), baseline_path(tmp_path, "2026-10-02"))
    assert find_latest_baseline(tmp_path, "2026-10-01") is None
    assert find_latest_baseline(tmp_path / "missing", "2026-10-01") is None


def test_days_since_basic():
    assert days_since("2026-10-26", "2026-10-29") == 3
    assert days_since("2026-10-29", "2026-10-29") == 0


def test_days_since_cross_month():
    assert days_since("2026-09-30", "2026-10-02") == 2


def test_days_since_future_release_raises():
    with pytest.raises(ValueError):
        days_since("2026-10-30", "2026-10-29")


def test_empty_baseline_matches_config_lines():
    config = make_config()
    baseline = empty_baseline("2026-10-29", config)
    assert baseline.date == "2026-10-29"
    assert [v.id for v in baseline.vendors] == [v.id for v in config.vendors]
    for vendor_state, vendor_config in zip(baseline.vendors, config.vendors):
        assert vendor_state.seen_directory_ids == []
        assert [line.key for line in vendor_state.lines] == [
            line.key for line in vendor_config.lines
        ]
        for line_state in vendor_state.lines:
            assert line_state.status == "pending"
            assert line_state.model is None
            assert line_state.release_date is None
            assert line_state.url is None
            assert line_state.last_verified is None
            assert line_state.reason == ""
            assert line_state.pending_days == 0


def test_line_state_defaults_pending():
    line = LineState(key="flagship")
    assert line.model is None
    assert line.release_date is None
    assert line.url is None
    assert line.last_verified is None
    assert line.status == "pending"
    assert line.reason == ""
    assert line.pending_days == 0


def test_baseline_rejects_unknown_status():
    with pytest.raises(ValidationError):
        LineState(key="flagship", status="broken")
