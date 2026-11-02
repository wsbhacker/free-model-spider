import time
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from free_model_spider.core.vendor_config import (
    ModelConfig,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.core.vendor_stats import (
    DirectoryRecord,
    ModelState,
    VendorState,
    Baseline,
    baseline_path,
    collect_directory_entries,
    days_since,
    discover_unmaintained,
    empty_baseline,
    find_latest_baseline,
    is_variant_record,
    load_baseline,
    model_matches,
    save_baseline,
    valid_directory_date,
)


def make_config() -> VendorReleasesConfig:
    return VendorReleasesConfig(
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"]},
                models=[
                    ModelConfig(
                        key="gpt-6-1-terra",
                        name="GPT-6.1 Terra",
                        match=[r"gpt[ -]?6\.1[ -]?terra"],
                        exclude=["pro"],
                    ),
                    ModelConfig(
                        key="gpt-6-1-mini",
                        name="GPT-6.1 mini",
                        match=[r"gpt[ -]?6\.1[ -]?mini"],
                    ),
                ],
            ),
            VendorConfig(
                id="zhipu",
                name="智谱 AI",
                official_domains=["z.ai"],
                directory_slugs={"openrouter": ["z-ai"]},
                models=[ModelConfig(key="glm-5-3", name="GLM-5.3", match=[r"glm[ -]?5\.3"])],
            ),
        ],
    )


def make_baseline() -> Baseline:
    return Baseline(
        date="2026-10-29",
        vendors=[
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-6.1-terra"],
                models=[
                    ModelState(
                        key="gpt-6-1-terra",
                        name="GPT-6.1 Terra",
                        date="2026-10-26",
                        url="https://openai.com/gpt-6-1-terra",
                        source="directory",
                        status="confirmed",
                        reason="目录日期（openrouter）",
                        last_verified="2026-10-29",
                    ),
                    ModelState(key="gpt-6-1-mini", name="GPT-6.1 mini",
                               status="unconfirmed", reason="LLM 未配置（暂态，次日重试）"),
                ],
            ),
            VendorState(
                id="zhipu",
                models=[ModelState(key="glm-5-3", name="GLM-5.3")],
            ),
        ],
    )


def test_baseline_path_uses_v3_directory(tmp_path):
    assert baseline_path(tmp_path, "2026-10-29") == (
        tmp_path / "vendor-models" / "2026-10-29.json"
    )


def test_save_and_load_roundtrip_preserves_structure(tmp_path):
    baseline = make_baseline()
    path = baseline_path(tmp_path, baseline.date)
    save_baseline(baseline, path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "目录日期（openrouter）" in text  # ensure_ascii=False
    assert '"schema_version": 1' in text  # schema 头落盘
    loaded = load_baseline(path)
    assert loaded == baseline
    openai = loaded.vendors[0]
    assert openai.seen_directory_ids == ["openai/gpt-6.1-terra"]
    terra, mini = openai.models
    assert (terra.date, terra.url, terra.source, terra.status) == (
        "2026-10-26", "https://openai.com/gpt-6-1-terra", "directory", "confirmed")
    assert mini.status == "unconfirmed"
    assert "暂态" in mini.reason
    assert loaded.vendors[1].models[0].status == "unconfirmed"


def test_find_latest_baseline_picks_max_date_le_on_or_before(tmp_path):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        save_baseline(make_baseline(), baseline_path(tmp_path, day))
    latest = find_latest_baseline(tmp_path, "2026-10-01")
    assert latest is not None and latest.stem == "2026-09-30"
    # 同日幂等重跑：stem == on_or_before 也要命中
    same_day = find_latest_baseline(tmp_path, "2026-09-30")
    assert same_day is not None and same_day.stem == "2026-09-30"


def test_find_latest_baseline_ignores_dir_without_json(tmp_path):
    (tmp_path / "vendor-models").mkdir()
    assert find_latest_baseline(tmp_path, "2026-10-01") is None


def test_find_latest_baseline_none_when_all_after_on_or_before(tmp_path):
    save_baseline(make_baseline(), baseline_path(tmp_path, "2026-10-02"))
    assert find_latest_baseline(tmp_path, "2026-10-01") is None
    assert find_latest_baseline(tmp_path / "missing", "2026-10-01") is None


def test_days_since_basic():
    assert days_since("2026-10-26", "2026-10-29") == 3
    assert days_since("2026-10-29", "2026-10-29") == 0
    with pytest.raises(ValueError):
        days_since("2026-10-30", "2026-10-29")


def test_empty_baseline_matches_config_models():
    config = make_config()
    baseline = empty_baseline("2026-10-29", config)
    assert baseline.date == "2026-10-29"
    for vendor_state, vendor_config in zip(baseline.vendors, config.vendors):
        assert vendor_state.seen_directory_ids == []
        assert [(m.key, m.name) for m in vendor_state.models] == [
            (m.key, m.name) for m in vendor_config.models
        ]
        for model_state in vendor_state.models:
            assert model_state.status == "unconfirmed"
            assert model_state.date is None
            assert model_state.url is None
            assert model_state.source is None


def test_model_state_defaults_and_validation():
    model = ModelState(key="glm-5-3", name="GLM-5.3")
    assert model.status == "unconfirmed"
    assert model.reason == ""
    assert model.last_verified is None
    with pytest.raises(ValidationError):
        ModelState(key="x", name="X", status="broken")
    with pytest.raises(ValidationError):
        ModelState(key="x", name="X", source="wildguess")


# --------------------------------------------------------------------------
# 模型窄匹配与未维护新模型发现
# --------------------------------------------------------------------------


def test_model_matches_regex_and_exclude():
    terra = ModelConfig(
        key="gpt-6-1-terra", name="GPT-6.1 Terra",
        match=[r"gpt[ -]?6\.1[ -]?terra"], exclude=["pro"])
    assert model_matches(terra, "openai/gpt-6.1-terra")
    assert model_matches(terra, "gpt 6.1 terra 发布")  # 空格形态
    assert not model_matches(terra, "openai/gpt-6.1-terra-pro")  # exclude 挡
    assert not model_matches(terra, "openai/gpt-6.2-terra")
    # exclude 大小写不敏感：PRO 也挡
    assert not model_matches(terra, "openai/gpt-6.1-terra-PRO".casefold())


def test_discover_unmaintained_filters_seen_and_maintained():
    config = make_config()
    openai = config.vendors[0]
    state = VendorState(id="openai", seen_directory_ids=["openai/gpt-6.1-sol"])
    records = [
        DirectoryRecord("openai/gpt-6.1-terra", "2026-10-26", "openrouter"),  # 已维护
        DirectoryRecord("openai/gpt-6.1-sol", None, "openrouter"),            # 已见
        DirectoryRecord("openai/gpt-7", "2026-10-30", "openrouter"),          # 未维护新模型
    ]
    assert discover_unmaintained(openai, state, records) == ["openai/gpt-7"]


def test_discover_unmaintained_dedupes_casefold():
    config = make_config()
    openai = config.vendors[0]
    state = VendorState(id="openai")
    records = [
        DirectoryRecord("openai/GPT-7", None, "openrouter"),
        DirectoryRecord("openai/gpt-7", None, "openrouter"),
    ]
    assert discover_unmaintained(openai, state, records) == ["openai/GPT-7"]


def test_valid_directory_date_guards():
    assert valid_directory_date("2026-10-26", "2026-11-01")
    assert valid_directory_date("2026-11-01", "2026-11-01")  # 今天可用
    assert not valid_directory_date(None, "2026-11-01")
    assert not valid_directory_date("2024-01-01", "2026-11-01")  # models.dev 占位符
    assert not valid_directory_date("2026-11-02", "2026-11-01")  # 未来
    assert not valid_directory_date("2019-12-31", "2026-11-01")  # 早于下限


def test_is_variant_record_only_openrouter_colon_suffix():
    assert is_variant_record(
        DirectoryRecord("openai/gpt-6.1:batch", None, "openrouter"))
    assert not is_variant_record(
        DirectoryRecord("openai/gpt-6.1", None, "openrouter"))
    # modelsdev 的 ':' 是 provider 分隔符，不算变体
    assert not is_variant_record(
        DirectoryRecord("openai:gpt-6.1", None, "modelsdev"))


# --------------------------------------------------------------------------
# 目录双源采集（v3：条目携带各自源日期）
# --------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"


def make_dir_config() -> VendorReleasesConfig:
    return VendorReleasesConfig(
        vendors=[
            VendorConfig(
                id="openai",
                name="OpenAI",
                official_domains=["openai.com"],
                directory_slugs={"openrouter": ["openai"], "modelsdev": ["openai"]},
                models=[ModelConfig(key="k", name="n", match=[r"gpt-\d"])],
            ),
            VendorConfig(
                id="mistral",
                name="Mistral",
                official_domains=["mistral.ai"],
                directory_slugs={"openrouter": ["mistralai"], "modelsdev": ["mistral"]},
                models=[ModelConfig(key="k", name="n", match=["mistral-large"])],
            ),
        ],
    )


def openrouter_payload() -> dict:
    # created=1798185600 → 2026-12-25（UTC）
    return {
        "data": [
            {"id": "openai/gpt-6.1", "created": 1798185600},
            {"id": "openai/gpt-6.1:batch", "created": 1798185600},
            {"id": "mistralai/mistral-large-3", "created": None},
            {"id": "unknown-vendor/whatever", "created": 1798185600},
        ]
    }


def modelsdev_payload() -> dict:
    return {
        "openai": {"models": {
            "gpt-6.1": {"release_date": "2026-12-24"},  # 与 openrouter 相差一天
            "gpt-6.1-chat": {},                           # 无 release_date
        }},
        "minimax": {"models": {"MiniMax-m3": {}}},        # 未映射 provider → 不归属
        "empty": {"id": "empty"},                          # 无 models 键 → 跳过
    }


@respx.mock
def test_collect_directory_entries_carries_dates():
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=openrouter_payload()))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=modelsdev_payload()))
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert errors == []
    assert entries["openai"] == [
        DirectoryRecord("openai/gpt-6.1", "2026-12-25", "openrouter"),
        DirectoryRecord("openai/gpt-6.1:batch", "2026-12-25", "openrouter"),
        DirectoryRecord("openai:gpt-6.1", "2026-12-24", "modelsdev"),
        DirectoryRecord("openai:gpt-6.1-chat", None, "modelsdev"),
    ]
    assert entries["mistral"] == [
        DirectoryRecord("mistralai/mistral-large-3", None, "openrouter"),
    ]


@respx.mock
def test_collect_degrades_when_one_directory_fails(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # 重试退避不真睡
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=modelsdev_payload()))
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert len(errors) == 1
    assert "openrouter" in errors[0]
    # 存活目录照常归属
    assert entries["mistral"] == []
    assert DirectoryRecord("openai:gpt-6.1", "2026-12-24", "modelsdev") in entries["openai"]


@respx.mock
def test_collect_returns_empty_when_both_directories_fail(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(500))
    entries, errors = collect_directory_entries(httpx.Client(), make_dir_config())
    assert entries == {}
    assert len(errors) == 2
