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
from free_model_spider.core.vendor_judge import Judgment
from free_model_spider.core.vendor_stats import (
    Baseline,
    LineState,
    VendorState,
    apply_judgment,
    baseline_path,
    collect_directory_entries,
    days_since,
    empty_baseline,
    finalize_state,
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
    assert openai.seen_announcement_urls == []  # v2 字段默认空，roundtrip 不丢
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


def test_screen_new_entries_per_line_exclude_does_not_kill_other_lines():
    """v2 修复钉子：旗舰 exclude 里的 flash 不得误杀 sub 线候选（v1 并集 bug，
    9 家厂商次旗舰线被全部误杀——deepseek flash 条目被旗舰 exclude=flash 挡掉）。"""
    vendor = VendorConfig(
        id="deepseek",
        name="DeepSeek",
        official_domains=["deepseek.com"],
        directory_slugs={"openrouter": ["deepseek"]},
        lines=[
            LineConfig(
                key="flagship",
                name="DeepSeek V/R 主线",
                match=[r"deepseek-(v\d|r\d|chat|reasoner)"],
                exclude=["flash", "coder", "vision"],
            ),
            LineConfig(
                key="sub",
                name="DeepSeek Flash",
                match=[r"deepseek-(v[\d.]+|[\d.]+)-?flash"],
                exclude=["coder", "vision"],
            ),
        ],
    )
    new = screen_new_entries(
        vendor, VendorState(id="deepseek"),
        ["deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-pro"],
    )
    assert set(new) == {
        "deepseek/deepseek-v4.1-flash",  # 只命中 sub（不被旗舰的 exclude=flash 误杀）
        "deepseek/deepseek-v4-pro",      # 只命中 flagship
    }











# --------------------------------------------------------------------------
# Task 5: 裁决编排、防倒退与 pending 状态机
# --------------------------------------------------------------------------


def make_judgment(
    model: str = "gpt-5.2",
    release_date: str = "2026-10-27",
    url: str = "https://openai.com/blog/gpt-5-2",
) -> Judgment:
    return Judgment(
        model=model,
        release_date=release_date,
        source_urls=[url],
        evidence=[f"官方公告：发布于 {release_date}"],
        reason="",
    )


def test_apply_judgment_forward_date_updates_baseline():
    """日期前进：整体更新 model/url/release_date 并重臂。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        last_verified="2026-10-28",
        status="ok",
    )
    new = apply_judgment(state, make_judgment(), "2026-10-29")
    assert new.model == "gpt-5.2"
    assert new.release_date == "2026-10-27"
    assert new.url == "https://openai.com/blog/gpt-5-2"
    assert new.status == "ok"
    assert new.pending_days == 0
    assert new.last_verified == "2026-10-29"
    # 纯函数：原对象不变
    assert state.model == "gpt-5.1"
    assert state.release_date == "2026-10-26"
    assert state.url == "https://openai.com/blog/gpt-5-1"
    assert state.last_verified == "2026-10-28"


def test_apply_judgment_same_day_rename_updates_model_and_url_only():
    """同日不同名：命名修正，仅更新 model/url，release_date 不动。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1-preview",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        status="pending",
        pending_days=2,
        last_verified="2026-10-20",
    )
    new = apply_judgment(
        state,
        make_judgment(
            model="gpt-5.1",
            release_date="2026-10-26",
            url="https://openai.com/blog/gpt-5-1-final",
        ),
        "2026-10-29",
    )
    assert new.model == "gpt-5.1"
    assert new.release_date == "2026-10-26"
    assert new.url == "https://openai.com/blog/gpt-5-1-final"
    assert new.status == "ok"
    assert new.pending_days == 0
    assert new.last_verified == "2026-10-29"


def test_apply_judgment_same_day_same_model_rearms_only():
    """同日同名：复核一致，不改基线值，仅重臂清 pending。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        status="pending",
        pending_days=3,
    )
    new = apply_judgment(
        state,
        make_judgment(model="gpt-5.1", release_date="2026-10-26"),
        "2026-10-29",
    )
    assert new.model == "gpt-5.1"
    assert new.release_date == "2026-10-26"
    assert new.url == "https://openai.com/blog/gpt-5-1"
    assert new.status == "ok"
    assert new.pending_days == 0
    assert new.last_verified == "2026-10-29"


def test_apply_judgment_rejects_backdated_release():
    """日期回退：拒绝（原值保留），转 pending 计数，reason 固定'防倒退拒绝'。"""
    state = LineState(
        key="flagship",
        model="gpt-5.2",
        release_date="2026-10-27",
        url="https://openai.com/blog/gpt-5-2",
        last_verified="2026-10-28",
        status="ok",
    )
    new = apply_judgment(
        state, make_judgment(model="gpt-5.1", release_date="2026-10-20"), "2026-10-29"
    )
    assert new.model == "gpt-5.2"
    assert new.release_date == "2026-10-27"
    assert new.url == "https://openai.com/blog/gpt-5-2"
    assert new.status == "pending"
    assert new.reason == "防倒退拒绝"
    assert new.pending_days == 1  # 拒绝日计入 pending，7 天未决仍会冻结
    assert new.last_verified == "2026-10-28"


def test_apply_judgment_first_day_writes_judgment_directly():
    """首日确认：state.release_date 为 None，有效裁决直接写入。"""
    state = LineState(key="flagship", status="pending", pending_days=4)
    new = apply_judgment(state, make_judgment(), "2026-10-29")
    assert new.model == "gpt-5.2"
    assert new.release_date == "2026-10-27"
    assert new.url == "https://openai.com/blog/gpt-5-2"
    assert new.status == "ok"
    assert new.pending_days == 0
    assert new.last_verified == "2026-10-29"


def test_apply_judgment_none_keeps_baseline_and_counts_pending():
    """judgment=None（提取失败/校验不过/确认失败）：原基线保留，pending_days+1。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        last_verified="2026-10-28",
        status="ok",
    )
    new = apply_judgment(state, None, "2026-10-29")
    assert new.model == "gpt-5.1"
    assert new.release_date == "2026-10-26"
    assert new.url == "https://openai.com/blog/gpt-5-1"
    assert new.status == "pending"
    assert new.pending_days == 1
    assert new.last_verified == "2026-10-28"
    assert new.reason  # 记录来源环节（提取/校验/确认失败降级）
    # 空线收到 None 同样计数：从未确认的线继续 pending
    fresh = apply_judgment(LineState(key="sub"), None, "2026-10-29")
    assert fresh.model is None
    assert fresh.status == "pending"
    assert fresh.pending_days == 1


def test_apply_judgment_partial_fields_treated_as_missing():
    """事实字段不全的裁决按缺失处理（防御：与 extract_line 的 None 契约一致）。"""
    state = LineState(key="flagship")
    new = apply_judgment(
        state, Judgment(model="gpt-5.2", release_date=None), "2026-10-29"
    )
    assert new.model is None
    assert new.status == "pending"
    assert new.pending_days == 1


def test_apply_judgment_future_date_treated_as_missing():
    """未来日期裁决视同缺失：pending 计数、基线值不变（防 days_since 下游崩溃）。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        last_verified="2026-10-28",
        status="ok",
    )
    new = apply_judgment(
        state, make_judgment(model="gpt-5.2", release_date="2026-10-30"), "2026-10-29"
    )
    assert new.model == "gpt-5.1"
    assert new.release_date == "2026-10-26"
    assert new.url == "https://openai.com/blog/gpt-5-1"
    assert new.status == "pending"
    assert new.reason == "未来日期拒绝"
    assert new.pending_days == 1
    assert new.last_verified == "2026-10-28"
    # 纯函数：原对象不变
    assert state.release_date == "2026-10-26"


def test_apply_judgment_first_day_future_date_not_written():
    """首日分支同样不写未来日期：空线保持空值并转 pending。"""
    new = apply_judgment(
        LineState(key="flagship"),
        make_judgment(release_date="2026-10-30"),
        "2026-10-29",
    )
    assert new.model is None
    assert new.release_date is None
    assert new.url is None
    assert new.status == "pending"
    assert new.reason == "未来日期拒绝"
    assert new.pending_days == 1


def test_apply_judgment_today_date_still_writes():
    """边界：release_date == today 合法，正常写入（未来日期拒绝只拦 > today）。"""
    new = apply_judgment(
        LineState(key="flagship"),
        make_judgment(release_date="2026-10-29"),
        "2026-10-29",
    )
    assert new.model == "gpt-5.2"
    assert new.release_date == "2026-10-29"
    assert new.status == "ok"
    assert new.last_verified == "2026-10-29"


def test_finalize_state_rearms_ok_line():
    """复核收尾：ok 线刷新 last_verified（重臂），status 不变。"""
    state = LineState(
        key="flagship",
        model="gpt-5.1",
        release_date="2026-10-26",
        url="https://openai.com/blog/gpt-5-1",
        last_verified="2026-08-10",
        status="ok",
    )
    new = finalize_state(state, "2026-10-29")
    assert new.status == "ok"
    assert new.last_verified == "2026-10-29"
    assert new.model == "gpt-5.1"
    assert state.last_verified == "2026-08-10"  # 纯函数


def test_finalize_state_freezes_pending_after_seven_days():
    """pending 满 7 天冻结为 stale-baseline；未满 7 天保持 pending 原样。"""
    due = finalize_state(
        LineState(key="flagship", status="pending", pending_days=7, reason="裁决缺失"),
        "2026-10-29",
    )
    assert due.status == "stale-baseline"
    assert due.pending_days == 7  # 冻结保留计数供审计
    assert due.reason
    assert due.last_verified == "2026-10-29"  # 冻结即重臂（stale 窗口不再永真）
    overdue = finalize_state(
        LineState(key="flagship", status="pending", pending_days=9), "2026-10-29"
    )
    assert overdue.status == "stale-baseline"
    short = finalize_state(
        LineState(key="flagship", status="pending", pending_days=6), "2026-10-29"
    )
    assert short.status == "pending"
    assert short.pending_days == 6
    assert short.last_verified is None  # 未冻结不刷 last_verified


def test_finalize_state_keeps_frozen_status_but_rearms_last_verified():
    """已冻结线收尾：status 保持 stale-baseline 不变，但 last_verified 重臂为 today
    （防 _line_is_stale 永真 → detect_review 每日重触发该厂商烧配额，spec §7.2）。"""
    state = LineState(
        key="sub",
        status="stale-baseline",
        reason="pending 连续 7 天未决，冻结",
        pending_days=60,
        last_verified="2026-08-01",
    )
    new = finalize_state(state, "2026-10-29")
    assert new.status == "stale-baseline"  # 冻结态不变
    assert new.reason == "pending 连续 7 天未决，冻结"
    assert new.pending_days == 60
    assert new.last_verified == "2026-10-29"  # 仅重臂
    assert state.last_verified == "2026-08-01"  # 纯函数






