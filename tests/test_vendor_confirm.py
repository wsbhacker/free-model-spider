"""v3 确认管线测试：通道优先级（manual > 公告提取 > models.dev > OpenRouter）。"""

import httpx
import respx

from free_model_spider.core.vendor_config import (
    AnnouncementSource,
    ManualRef,
    ModelConfig,
    VendorConfig,
)
from free_model_spider.core.vendor_confirm import resolve_model
from free_model_spider.core.vendor_source import AnnouncementEntry
from free_model_spider.core.vendor_stats import DirectoryRecord

ARTICLE_URL = "https://docs.z.ai/release-notes/glm-5-3"

VENDOR = VendorConfig(
    id="zai",
    name="智谱 AI",
    official_domains=["z.ai"],
    directory_slugs={"openrouter": ["z-ai"], "modelsdev": ["zai"]},
    announcements=[AnnouncementSource(
        name="发布说明", url="https://docs.z.ai/release-notes/new-released",
        mode="headings")],
    models=[ModelConfig(key="glm-5-3", name="GLM-5.3", match=[r"glm[ -]?5\.3"])],
)
MODEL = VENDOR.models[0]

NO_SOURCE_VENDOR = VendorConfig(
    id="openai",
    name="OpenAI",
    official_domains=["openai.com"],
    directory_slugs={"openrouter": ["openai"], "modelsdev": ["openai"]},
    models=[ModelConfig(key="gpt-6-1-terra", name="GPT-6.1 Terra",
                        match=[r"gpt[ -]?6\.1[ -]?terra"])],
)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0)


def _listings(*entries):
    return [AnnouncementEntry(title=t, url=u, date=d) for t, u, d in entries]


def test_manual_ref_takes_precedence_over_everything():
    manual_model = ModelConfig(
        key="glm-5-3", name="GLM-5.3", match=[r"glm"],
        manual=ManualRef(date="2026-08-18", url="https://z.ai/glm-5-3"))
    state = resolve_model(VENDOR, manual_model, [], [], None, httpx.Client(), "2026-11-01")
    assert (state.status, state.source, state.date, state.url) == (
        "confirmed", "manual", "2026-08-18", "https://z.ai/glm-5-3")


@respx.mock
def test_official_channel_confirms_with_article_url():
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(
        200, text="<html><body>GLM-5.3 于 2026-08-18 正式发布。</body></html>"))
    llm = FakeLLM(['{"model": "GLM-5.3", "release_date": "2026-08-18", '
                   '"evidence": ["GLM-5.3 于 2026-08-18 正式发布"], "reason": ""}'])
    listings = _listings(
        ("GLM-5.2 Release", "https://docs.z.ai/release-notes", "2026-06-16"),  # 不匹配
        ("GLM-5.3 Release", ARTICLE_URL, "2026-08-18"),                        # 命中
        ("GLM-5.3 旧公告", ARTICLE_URL, "2026-08-01"),                          # 命中但更旧
    )
    state = resolve_model(VENDOR, MODEL, listings, [], llm, httpx.Client(), "2026-11-01")
    assert state.status == "confirmed"
    assert state.source == "official"
    assert state.date == "2026-08-18"
    assert state.url == ARTICLE_URL
    assert len(llm.calls) == 1  # 只对日期最新命中条目提取一次


@respx.mock
def test_extraction_failure_stays_unconfirmed_without_directory_fallback():
    """暂态铁律：提取失败不降级目录通道（confirmed 永固，降级不可逆）。"""
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, text="页面无日期"))
    llm = FakeLLM(['{"model": "GLM-5.3", "release_date": null, "evidence": [], '
                   '"reason": "页面原文未出现发布日期"}'])
    listings = _listings(("GLM-5.3 Release", ARTICLE_URL, "2026-08-18"))
    records = [DirectoryRecord("z-ai/glm-5.3", "2026-08-18", "openrouter")]
    state = resolve_model(VENDOR, MODEL, listings, records, llm, httpx.Client(),
                          "2026-11-01")
    assert state.status == "unconfirmed"
    assert "暂态" in state.reason
    assert state.date is None


def test_no_matching_entry_falls_through_to_directory():
    """结构性缺席：列表抓取成功但无匹配条目 → 目录日期（modelsdev 优先）。"""
    listings = _listings(("无关公告", "https://docs.z.ai/x", "2026-10-01"))
    records = [
        DirectoryRecord("z-ai/glm-5.3", "2026-08-20", "openrouter"),
        DirectoryRecord("zai:glm-5.3", "2026-08-18", "modelsdev"),  # 策展日优先
    ]
    state = resolve_model(VENDOR, MODEL, listings, records, None, httpx.Client(),
                          "2026-11-01")
    assert (state.status, state.source, state.date, state.url) == (
        "confirmed", "directory", "2026-08-18", None)


def test_listing_unavailable_is_transient_not_structural():
    """公告源当日不可用（listings=None）→ unconfirmed 次日重试，不落目录。"""
    records = [DirectoryRecord("z-ai/glm-5.3", "2026-08-20", "openrouter")]
    state = resolve_model(VENDOR, MODEL, None, records, None, httpx.Client(),
                          "2026-11-01")
    assert state.status == "unconfirmed"
    assert "公告源不可用" in state.reason


def test_directory_placeholder_rejected_then_openrouter_used():
    """models.dev 占位 2024-01-01 拒绝 → 落 OpenRouter created；:batch 变体不算。"""
    records = [
        DirectoryRecord("openai:gpt-6.1-terra", "2024-01-01", "modelsdev"),
        DirectoryRecord("openai/gpt-6.1-terra:batch", "2026-10-27", "openrouter"),
        DirectoryRecord("openai/gpt-6.1-terra", "2026-10-26", "openrouter"),
    ]
    state = resolve_model(NO_SOURCE_VENDOR, NO_SOURCE_VENDOR.models[0], [], records,
                          None, httpx.Client(), "2026-11-01")
    assert state.source == "directory"
    assert state.date == "2026-10-26"


def test_no_source_vendor_resolves_from_directory_directly():
    """无公告源厂商（openai/qwen/moonshot）直接走目录通道，0 LLM。"""
    records = [DirectoryRecord("openai/gpt-6.1-terra", "2026-10-26", "openrouter")]
    state = resolve_model(NO_SOURCE_VENDOR, NO_SOURCE_VENDOR.models[0], [], records,
                          None, httpx.Client(), "2026-11-01")
    assert (state.status, state.source, state.date) == (
        "confirmed", "directory", "2026-10-26")


def test_neither_channel_has_date_stays_unconfirmed():
    state = resolve_model(NO_SOURCE_VENDOR, NO_SOURCE_VENDOR.models[0], [], [],
                          None, httpx.Client(), "2026-11-01")
    assert state.status == "unconfirmed"
    assert "均无可用日期" in state.reason


def test_directory_future_date_rejected():
    records = [DirectoryRecord("openai/gpt-6.1-terra", "2026-11-05", "openrouter")]
    state = resolve_model(NO_SOURCE_VENDOR, NO_SOURCE_VENDOR.models[0], [], records,
                          None, httpx.Client(), "2026-11-01")
    assert state.status == "unconfirmed"


def test_llm_not_configured_with_matching_entry_is_transient():
    listings = _listings(("GLM-5.3 Release", ARTICLE_URL, "2026-08-18"))
    state = resolve_model(VENDOR, MODEL, listings, [], None, httpx.Client(),
                          "2026-11-01")
    assert state.status == "unconfirmed"
    assert "LLM 未配置" in state.reason
