import time
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from free_model_spider.core.vendor_config import (
    LineConfig,
    Override,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.core.vendor_stats import (
    Baseline,
    LineState,
    VendorState,
    baseline_path,
    collect_directory_entries,
    days_since,
    detect_review,
    empty_baseline,
    find_latest_baseline,
    load_baseline,
    save_baseline,
    screen_new_entries,
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


# --------------------------------------------------------------------------
# Task 3: 目录采集与事件检测
# --------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"


def make_dir_config() -> VendorReleasesConfig:
    """目录采集/事件检测测试配置：openai、google、mistral 三家覆盖两目录。"""
    return VendorReleasesConfig(
        stale_days=45,
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"], "modelsdev": ["openai"]},
                lines=[
                    LineConfig(
                        key="flagship",
                        name="GPT 主线",
                        match=[r"gpt-\d"],
                        exclude=["mini", "nano", "image"],
                    ),
                ],
            ),
            VendorConfig(
                id="google",
                name="Google DeepMind",
                official_domains=["deepmind.google"],
                directory_slugs={"openrouter": ["google"], "modelsdev": ["google"]},
                lines=[
                    LineConfig(
                        key="flagship",
                        name="Gemini 主线",
                        match=[r"gemini-\d"],
                        exclude=["flash", "image", "video"],
                    ),
                    LineConfig(key="sub", name="Gemini Flash", match=["flash"]),
                ],
            ),
            VendorConfig(
                id="mistral",
                name="Mistral",
                official_domains=["mistral.ai"],
                directory_slugs={"openrouter": ["mistralai"], "modelsdev": ["mistral"]},
                lines=[
                    LineConfig(key="flagship", name="Mistral Large", match=["mistral-large"]),
                ],
            ),
        ],
    )


def openrouter_payload() -> dict:
    return {
        "data": [
            {"id": "openai/gpt-5.2"},
            {"id": "openai/gpt-5.2-mini"},
            {"id": "openai/gpt-5.2"},  # 重复条目 → 去重
            {"id": "google/gemini-4"},
            {"id": "mistralai/mistral-large-3"},
            {"id": "unknown-vendor/whatever"},
        ]
    }


def modelsdev_payload() -> dict:
    return {
        "openai": {"models": {"gpt-5.2": {"name": "GPT"}, "gpt-5.2-chat": {}}},
        "mistral": {"models": {"deepseek-v4-1-flash": {}}},
        "minimax": {"models": {"MiniMax-M2": {}}},  # 配置未映射该 provider → 不归属
        "meta": {"models": {"llama-4": {}}},
        "empty": {"id": "empty"},  # 无 models 键 → 跳过
    }


@respx.mock
def test_collect_directory_entries_merges_and_attributes():
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=openrouter_payload())
    )
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=modelsdev_payload())
    )
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert errors == []
    assert entries["openai"] == [
        "openai/gpt-5.2",
        "openai/gpt-5.2-mini",
        "openai:gpt-5.2",
        "openai:gpt-5.2-chat",
    ]
    assert entries["google"] == ["google/gemini-4"]
    # models.dev 条目按 provider（modelsdev slug）挂靠，id 内容不参与归属
    assert entries["mistral"] == [
        "mistralai/mistral-large-3",
        "mistral:deepseek-v4-1-flash",
    ]


@respx.mock
def test_collect_degrades_when_one_directory_fails(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # 重试退避不真睡
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=modelsdev_payload())
    )
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert len(errors) == 1
    assert "openrouter" in errors[0]
    assert "openai/gpt-5.2" not in entries["openai"]  # 挂掉的目录无条目
    assert entries["mistral"] == ["mistral:deepseek-v4-1-flash"]  # 存活目录照常归属


@respx.mock
def test_collect_returns_empty_when_both_directories_fail(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(500))
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert entries == {}
    assert len(errors) == 2
    assert "openrouter" in errors[0]
    assert "models.dev" in errors[1]


def test_screen_new_entries_casefold_match_exclude_and_seen():
    config = make_dir_config()
    openai = config.vendors[0]
    state = VendorState(id="openai", seen_directory_ids=["OpenAI/gpt-5.1"])
    new = screen_new_entries(
        openai,
        state,
        [
            "openai/gpt-5.2",
            "openai/gpt-5.2-mini",  # exclude=mini 挡住
            "openai/gpt-5.1",  # 已见（大小写不敏感）
            "OpenAI/GPT-5.3-Image",  # exclude=image 挡住（casefold 后匹配）
        ],
    )
    assert new == ["openai/gpt-5.2"]


def test_screen_new_entries_returns_casefolded_ids():
    """契约钉子：已映射 provider 的 models.dev 大写条目也须 casefold 匹配并返回
    casefold 后的 id（Task 5 依赖）；若无 casefold，gpt-\\d 不命中大写 GPT-5.2-PRO。"""
    config = make_dir_config()
    openai = config.vendors[0]
    state = VendorState(id="openai", seen_directory_ids=[])
    new = screen_new_entries(openai, state, ["openai:GPT-5.2-PRO"])
    assert new == ["openai:gpt-5.2-pro"]


def test_screen_new_entries_override_line_not_participating():
    vendor = VendorConfig(
        id="openai",
        name="OpenAI",
        official_domains=["openai.com"],
        directory_slugs={"openrouter": ["openai"]},
        lines=[
            LineConfig(
                key="flagship",
                name="GPT 主线",
                match=[r"gpt-\d"],
                override=Override(model="gpt-5.1", release_date="2026-05-01", source="人工核定"),
            ),
            LineConfig(key="sub", name="GPT mini", match=["mini"]),
        ],
    )
    state = VendorState(id="openai")
    new = screen_new_entries(
        vendor, state, ["openai/gpt-5.2", "openai/gpt-5.2-mini"]
    )
    # flagship 有 override 不参与：gpt-5.2 不命中任何线；mini 线仍命中
    assert new == ["openai/gpt-5.2-mini"]


def test_detect_review_new_entry_triggers():
    config = make_dir_config()
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-5.1", "openai/gpt-5.1-mini"],
                lines=[
                    LineState(
                        key="flagship",
                        release_date="2026-10-20",
                        last_verified="2026-10-29",
                        status="ok",
                    )
                ],
            )
        ],
    )
    reasons = detect_review(config, baseline, {"openai": ["openai/gpt-5.2"]}, "2026-10-29")
    assert reasons == {"openai": ["new-entry"]}


def test_detect_review_exclude_blocks_trigger():
    config = make_dir_config()
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="google",
                seen_directory_ids=[],
                lines=[
                    LineState(
                        key="flagship",
                        release_date="2026-10-20",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                    LineState(
                        key="sub",
                        release_date="2026-10-20",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                ],
            )
        ],
    )
    # gemini-4-image 命中旗舰 regex，但被 flash 之外的线（旗舰）的 exclude=image 挡住
    reasons = detect_review(config, baseline, {"google": ["google/gemini-4-image"]}, "2026-10-29")
    assert reasons == {}


def test_detect_review_modelsdev_entry_without_match_no_trigger():
    config = make_dir_config()
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="mistral",
                lines=[
                    LineState(
                        key="flagship",
                        release_date="2026-10-20",
                        last_verified="2026-10-29",
                        status="ok",
                    )
                ],
            )
        ],
    )
    # mistral:deepseek-v4-1-flash 挂靠在 mistral 厂商下，但不命中其 lines → 不触发
    reasons = detect_review(
        config, baseline, {"mistral": ["mistral:deepseek-v4-1-flash"]}, "2026-10-29"
    )
    assert reasons == {}


def test_detect_review_stale_triggers_then_rearm_after_review():
    config = make_dir_config()

    def make_state(last_verified: str) -> VendorState:
        return VendorState(
            id="openai",
            lines=[
                LineState(
                    key="flagship",
                    model="gpt-5.1",
                    release_date="2026-08-01",
                    last_verified=last_verified,
                    status="ok",
                )
            ],
        )

    # today - max(release_date, last_verified) = 2026-10-29 - 2026-08-10 = 80 > 45
    stale = detect_review(config, Baseline(date="2026-10-29", vendors=[make_state("2026-08-10")]), {}, "2026-10-29")
    assert stale == {"openai": ["stale"]}
    # 复核刷新 last_verified 后不再触发
    rearmed = detect_review(config, Baseline(date="2026-10-29", vendors=[make_state("2026-10-29")]), {}, "2026-10-29")
    assert rearmed == {}


def test_detect_review_stale_requires_all_lines_over_window():
    config = make_dir_config()
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="google",
                lines=[
                    LineState(  # 超窗
                        key="flagship",
                        release_date="2026-06-01",
                        last_verified="2026-06-01",
                        status="ok",
                    ),
                    LineState(  # 新鲜
                        key="sub",
                        release_date="2026-10-20",
                        last_verified="2026-10-29",
                        status="ok",
                    ),
                ],
            )
        ],
    )
    assert detect_review(config, baseline, {}, "2026-10-29") == {}


def test_detect_review_override_exempts_stale():
    config = VendorReleasesConfig(
        stale_days=45,
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"]},
                lines=[
                    LineConfig(
                        key="flagship",
                        name="GPT 主线",
                        match=[r"gpt-\d"],
                        override=Override(model="gpt-5.1", release_date="2026-05-01", source="人工核定"),
                    ),
                ],
            )
        ],
    )
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                lines=[
                    LineState(key="flagship", model="gpt-5.1", release_date="2026-05-01", status="ok")
                ],
            )
        ],
    )
    assert detect_review(config, baseline, {}, "2026-10-29") == {}


def test_detect_review_first_day_empty_baseline_all_stale():
    config = make_dir_config()
    baseline = empty_baseline("2026-10-29", config)
    reasons = detect_review(config, baseline, {}, "2026-10-29")
    assert reasons == {vendor.id: ["stale"] for vendor in config.vendors}


def test_detect_review_new_entry_and_stale_together():
    config = make_dir_config()
    baseline = Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-5.1"],
                lines=[
                    LineState(
                        key="flagship",
                        model="gpt-5.1",
                        release_date="2026-08-01",
                        last_verified="2026-08-10",
                        status="ok",
                    )
                ],
            )
        ],
    )
    reasons = detect_review(config, baseline, {"openai": ["openai/gpt-5.2"]}, "2026-10-29")
    assert reasons == {"openai": ["new-entry", "stale"]}
