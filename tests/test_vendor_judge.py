import httpx
import respx

from free_model_spider.core import vendor_judge
from free_model_spider.core.vendor_config import LineConfig, VendorConfig
from free_model_spider.core.vendor_judge import (
    SYSTEM_PROMPT,
    Judgment,
    classify_entry,
    confirm_from_page,
    extract_line,
    validate_judgment,
)
from free_model_spider.llm.client import LLMError
from free_model_spider.search.base import SearchProvider, SearchResult

VENDOR = VendorConfig(
    id="zai",
    name="智谱 AI",
    official_domains=["z.ai", "chat.z.ai", "docs.z.ai", "bigmodel.cn"],
    directory_slugs={"openrouter": ["z-ai"], "models.dev": ["zai"]},
    lines=[
        LineConfig(key="flagship", name="GLM 主线", match=[r"glm-\d"]),
        LineConfig(key="sub", name="GLM Flash", match=[r"glm.*(flash|flashx)"]),
    ],
)

PAGE_URL = "https://z.ai/blog/glm-5-3"

GOOD_SEARCH_RESULTS = [
    SearchResult(
        title="智谱正式发布 GLM-5.3",
        url=PAGE_URL,
        snippet="GLM-5.3 于 2026-09-24 正式发布。",
    )
]

GOOD_EXTRACT_JSON = (
    '{"model": "GLM-5.3", "release_date": "2026-09-24", '
    '"source_urls": ["https://z.ai/blog/glm-5-3"], '
    '"evidence": ["GLM-5.3 于 2026-09-24 正式发布 https://z.ai/blog/glm-5-3"], '
    '"reason": ""}'
)

CANDIDATE = Judgment(
    model="GLM-5.3",
    release_date="2026-09-24",
    source_urls=[PAGE_URL],
    evidence=["GLM-5.3 于 2026-09-24 正式发布"],
    reason="",
)


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, results=None, error: Exception | None = None):
        self.results = results or []
        self.error = error
        self.queries: list[str] = []
        self.counts: list[int] = []

    def search(self, query, count=5):
        self.queries.append(query)
        self.counts.append(count)
        if self.error:
            raise self.error
        return self.results


class FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# ---------- SYSTEM_PROMPT 基座 ----------


def test_system_prompt_carries_hard_constraints():
    # spec §7.1 五条硬约束的关键词逐条可寻址
    assert "材料" in SYSTEM_PROMPT
    assert "null" in SYSTEM_PROMPT
    assert "evidence" in SYSTEM_PROMPT
    assert "YYYY-MM-DD" in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT


# ---------- validate_judgment 五连 ----------


def test_validate_rejects_missing_evidence():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=[PAGE_URL],
        evidence=[],
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_date_absent_from_evidence_text():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=[PAGE_URL],
        evidence=["GLM-5.3 正式发布"],  # 不含日期字符串
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_non_whitelist_domain():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=["https://techcrunch.com/glm-5-3"],
        evidence=["GLM-5.3 于 2026-09-24 发布"],
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_accepts_all_none_with_reason():
    j = Judgment(
        model=None,
        release_date=None,
        source_urls=[],
        evidence=[],
        reason="搜索材料未提及发布日期",
    )
    assert validate_judgment(j, VENDOR.official_domains) is True


def test_validate_accepts_official_url_with_evidence_date():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=[PAGE_URL],
        evidence=["GLM-5.3 于 2026-09-24 正式发布"],
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is True


def test_validate_rejects_lookalike_subdomain():
    # evil-z.ai.com 不是 z.ai 的子域
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=["https://evil-z.ai.com/blog"],
        evidence=["GLM-5.3 于 2026-09-24 发布"],
        reason="",
    )
    assert validate_judgment(j, ["z.ai"]) is False


def test_validate_accepts_real_subdomain():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=["https://chat.z.ai/blog/glm"],
        evidence=["GLM-5.3 于 2026-09-24 发布"],
        reason="",
    )
    assert validate_judgment(j, ["z.ai"]) is True


def test_validate_rejects_malformed_date():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026/09/24",
        source_urls=[PAGE_URL],
        evidence=["日期是 2026/09/24"],
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_model_without_date():
    j = Judgment(
        model="GLM-5.3",
        release_date=None,
        source_urls=[PAGE_URL],
        evidence=["GLM-5.3 发布"],
        reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


# ---------- extract_line ----------


def test_extract_line_query_verbatim_and_count_8():
    search = FakeSearch(results=GOOD_SEARCH_RESULTS)
    llm = FakeLLM(replies=[GOOD_EXTRACT_JSON])
    judgment, results = extract_line(llm, search, VENDOR, VENDOR.lines[0], None)
    assert search.queries == [
        "site:z.ai OR site:chat.z.ai OR site:docs.z.ai 智谱 AI GLM 主线 发布"
    ]
    assert search.counts == [8]
    assert results == GOOD_SEARCH_RESULTS
    assert judgment is not None
    assert judgment.model == "GLM-5.3"
    assert judgment.release_date == "2026-09-24"


def test_extract_line_parses_fenced_json():
    llm = FakeLLM(replies=["```json\n" + GOOD_EXTRACT_JSON + "\n```"])
    judgment, _ = extract_line(llm, FakeSearch(GOOD_SEARCH_RESULTS), VENDOR, VENDOR.lines[0], None)
    assert judgment is not None
    assert judgment.source_urls == [PAGE_URL]
    assert llm.calls[0][0] is SYSTEM_PROMPT  # 共用 prompt 基座


def test_extract_line_two_bad_json_degrades():
    llm = FakeLLM(replies=["not json at all", '{"model": broken'])
    judgment, results = extract_line(llm, FakeSearch(GOOD_SEARCH_RESULTS), VENDOR, VENDOR.lines[0], None)
    assert judgment is None
    assert results == GOOD_SEARCH_RESULTS
    assert len(llm.calls) == 2  # 重试 1 次


def test_extract_line_llm_error_degrades_without_retry():
    llm = FakeLLM(replies=[LLMError("boom")])
    judgment, _ = extract_line(llm, FakeSearch(GOOD_SEARCH_RESULTS), VENDOR, VENDOR.lines[0], None)
    assert judgment is None
    assert len(llm.calls) == 1


def test_extract_line_validation_failure_returns_none():
    bad = (
        '{"model": "GLM-5.3", "release_date": "2026-09-24", '
        '"source_urls": ["https://techcrunch.com/a"], '
        '"evidence": ["GLM-5.3 于 2026-09-24 发布"], "reason": ""}'
    )
    judgment, results = extract_line(FakeLLM([bad]), FakeSearch(GOOD_SEARCH_RESULTS), VENDOR, VENDOR.lines[0], None)
    assert judgment is None
    assert results == GOOD_SEARCH_RESULTS


def test_extract_line_insufficient_material_returns_none():
    reply = (
        '{"model": null, "release_date": null, "source_urls": [], '
        '"evidence": [], "reason": "搜索结果未提及发布日期"}'
    )
    judgment, results = extract_line(FakeLLM([reply]), FakeSearch(GOOD_SEARCH_RESULTS), VENDOR, VENDOR.lines[0], None)
    assert judgment is None  # 材料不足降级 pending，不产出空裁决
    assert results == GOOD_SEARCH_RESULTS


def test_extract_line_search_none_returns_none_empty():
    judgment, results = extract_line(FakeLLM([GOOD_EXTRACT_JSON]), None, VENDOR, VENDOR.lines[0], None)
    assert (judgment, results) == (None, [])


def test_extract_line_search_error_returns_none_empty():
    search = FakeSearch(error=httpx.ConnectError("down"))
    judgment, results = extract_line(FakeLLM([GOOD_EXTRACT_JSON]), search, VENDOR, VENDOR.lines[0], None)
    assert (judgment, results) == (None, [])


def test_extract_line_no_llm_returns_none_with_results():
    search = FakeSearch(results=GOOD_SEARCH_RESULTS)
    judgment, results = extract_line(None, search, VENDOR, VENDOR.lines[0], None)
    assert judgment is None
    assert results == GOOD_SEARCH_RESULTS


def test_extract_line_sleep_called_after_search():
    events: list[tuple] = []

    class RecordingSearch(SearchProvider):
        name = "rec"

        def search(self, query, count=5):
            events.append(("search", query, count))
            return GOOD_SEARCH_RESULTS

    judgment, _ = extract_line(
        FakeLLM([GOOD_EXTRACT_JSON]),
        RecordingSearch(),
        VENDOR,
        VENDOR.lines[0],
        search_sleep=lambda seconds: events.append(("sleep", seconds)),
    )
    assert judgment is not None
    assert events == [
        ("search", "site:z.ai OR site:chat.z.ai OR site:docs.z.ai 智谱 AI GLM 主线 发布", 8),
        ("sleep", 1.1),
    ]


# ---------- classify_entry ----------


def test_classify_entry_returns_category_and_reason():
    llm = FakeLLM(replies=['{"category": "sub", "reason": "名称含 flashx，属次旗舰线"}'])
    assert classify_entry(llm, VENDOR, "zai/glm-5.3-flashx") == (
        "sub",
        "名称含 flashx，属次旗舰线",
    )


def test_classify_entry_parses_fenced_flagship():
    llm = FakeLLM(replies=['```json\n{"category": "flagship", "reason": "命中旗舰正则"}\n```'])
    assert classify_entry(llm, VENDOR, "zai/glm-5.3") == ("flagship", "命中旗舰正则")


def test_classify_entry_bad_json_twice_pending():
    llm = FakeLLM(replies=["nope", "still nope"])
    assert classify_entry(llm, VENDOR, "zai/whatever") == ("pending", "")
    assert len(llm.calls) == 2


def test_classify_entry_invalid_category_pending():
    llm = FakeLLM(replies=['{"category": "mainline", "reason": "x"}', '{"category": "", "reason": "y"}'])
    assert classify_entry(llm, VENDOR, "zai/whatever") == ("pending", "")


def test_classify_entry_llm_error_pending():
    llm = FakeLLM(replies=[LLMError("boom")])
    assert classify_entry(llm, VENDOR, "zai/whatever") == ("pending", "")


def test_classify_entry_no_llm_pending():
    assert classify_entry(None, VENDOR, "zai/whatever") == ("pending", "")


# ---------- confirm_from_page ----------


@respx.mock
def test_confirm_from_page_confirmed_from_page_text():
    respx.get(PAGE_URL).respond(
        200, text="智谱正式发布 GLM-5.3：GLM-5.3 于 2026-09-24 面向公众开放。"
    )
    llm = FakeLLM(replies=[GOOD_EXTRACT_JSON])
    out = confirm_from_page(llm, httpx.Client(), VENDOR, CANDIDATE)
    assert out is not None
    assert out.model == "GLM-5.3"
    assert out.release_date == "2026-09-24"
    assert out.source_urls == [PAGE_URL]  # 回填为被抓取的官方页
    assert llm.calls[0][0] is SYSTEM_PROMPT  # 同 prompt 基座
    assert "2026-09-24" in llm.calls[0][1]  # 页面原文进了 user prompt


@respx.mock
def test_confirm_from_page_truncates_body_to_8000():
    tail_marker = "TAIL-MARKER-BEYOND-LIMIT"
    body = "GLM-5.3 于 2026-09-24 正式发布。" + "x" * 8100 + tail_marker
    respx.get(PAGE_URL).respond(200, text=body)
    llm = FakeLLM(replies=[GOOD_EXTRACT_JSON])
    out = confirm_from_page(llm, httpx.Client(), VENDOR, CANDIDATE)
    assert out is not None
    user_prompt = llm.calls[0][1]
    assert "2026-09-24" in user_prompt
    assert tail_marker not in user_prompt


@respx.mock
def test_confirm_from_page_without_date_returns_none():
    respx.get(PAGE_URL).respond(200, text="本页介绍 GLM-5.3 的能力与定价，未写发布日期。")
    llm = FakeLLM(
        replies=[
            '{"model": "GLM-5.3", "release_date": null, "evidence": [], '
            '"reason": "页面原文未出现发布日期"}'
        ]
    )
    out = confirm_from_page(llm, httpx.Client(), VENDOR, CANDIDATE)
    assert out is None


@respx.mock
def test_confirm_from_page_evidence_without_date_returns_none():
    respx.get(PAGE_URL).respond(200, text="GLM-5.3 已发布。")
    llm = FakeLLM(
        replies=[
            '{"model": "GLM-5.3", "release_date": "2026-09-24", '
            '"evidence": ["GLM-5.3 已发布"], "reason": ""}'
        ]
    )
    out = confirm_from_page(llm, httpx.Client(), VENDOR, CANDIDATE)
    assert out is None  # evidence 不含日期，validate 不过


def test_confirm_from_page_network_error_returns_none(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(vendor_judge, "request_with_retries", boom)
    out = confirm_from_page(FakeLLM([GOOD_EXTRACT_JSON]), httpx.Client(), VENDOR, CANDIDATE)
    assert out is None


def test_confirm_from_page_empty_source_urls_returns_none():
    j = Judgment(
        model="GLM-5.3",
        release_date="2026-09-24",
        source_urls=[],
        evidence=["2026-09-24"],
        reason="",
    )
    llm = FakeLLM(replies=[])
    assert confirm_from_page(llm, httpx.Client(), VENDOR, j) is None
    assert llm.calls == []  # 未发起 LLM 调用


@respx.mock
def test_confirm_from_page_llm_error_returns_none():
    respx.get(PAGE_URL).respond(200, text="GLM-5.3 于 2026-09-24 发布。")
    llm = FakeLLM(replies=[LLMError("boom")])
    out = confirm_from_page(llm, httpx.Client(), VENDOR, CANDIDATE)
    assert out is None
