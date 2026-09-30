import httpx
import pytest
import respx

from free_model_spider.core.intro import IntroCard, build_intro, metadata_card
from free_model_spider.llm.client import LLMError
from free_model_spider.search.base import SearchProvider, SearchResult
from free_model_spider.sources.base import ModelRecord

REC = ModelRecord(
    source="openrouter",
    id="vendor/model-a:free",
    name="Vendor: Model A (free)",
    context_length=262144,
    input_modalities=["text"],
    output_modalities=["text"],
    created=1786722910,
    description="A great free model.",
    links={"platform_page": "https://openrouter.ai/vendor/model-a:free"},
)


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, results=None, error: Exception | None = None):
        self.results = results or []
        self.error = error
        self.queries: list[str] = []

    def search(self, query, count=5):
        self.queries.append(query)
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


GOOD_JSON = (
    '{"summary": "这是厂商 A 的免费模型。", '
    '"highlights": ["上下文 26 万", "支持文本"], '
    '"caveat": ""}'
)


def test_metadata_card_fields():
    card = metadata_card(REC)
    assert card.kind == "metadata"
    assert card.model_id == "vendor/model-a:free"
    assert "262,144" in card.summary or "262144" in card.summary
    assert card.sources == ["https://openrouter.ai/vendor/model-a:free"]


def test_build_intro_full_path():
    search = FakeSearch(results=[SearchResult(title="T", url="https://n.example/1", snippet="S")])
    llm = FakeLLM(replies=[GOOD_JSON])
    card = build_intro(REC, search, llm)
    assert card.kind == "full"
    assert card.summary == "这是厂商 A 的免费模型。"
    assert card.highlights == ["上下文 26 万", "支持文本"]
    assert "https://n.example/1" in card.sources
    assert search.queries == ["Vendor: Model A (free) vendor"]
    assert "只基于" in llm.calls[0][0]  # system prompt 硬约束


def test_build_intro_search_failure_still_full():
    search = FakeSearch(error=httpx.ConnectError("down"))
    llm = FakeLLM(replies=[GOOD_JSON])
    card = build_intro(REC, search, llm)
    assert card.kind == "full"
    assert card.sources == ["https://openrouter.ai/vendor/model-a:free"]


def test_build_intro_bad_json_then_retry_then_degrade():
    llm = FakeLLM(replies=["not json", "also not json"])
    card = build_intro(REC, FakeSearch(), llm)
    assert card.kind == "metadata"
    assert len(llm.calls) == 2  # 重试一次


def test_build_intro_llm_error_degrades():
    llm = FakeLLM(replies=[LLMError("boom")])
    card = build_intro(REC, FakeSearch(), llm)
    assert card.kind == "metadata"


def test_build_intro_no_llm_no_search():
    card = build_intro(REC, None, None)
    assert card.kind == "metadata"
    assert isinstance(card, IntroCard)


def test_build_intro_highlights_null_does_not_crash():
    llm = FakeLLM(replies=['{"summary": "ok", "highlights": null, "caveat": ""}'])
    card = build_intro(REC, FakeSearch(), llm)
    assert card.kind == "full"
    assert card.highlights == []


def test_build_intro_sources_deduped():
    search = FakeSearch(results=[
        SearchResult(title="T1", url="https://n.example/1", snippet="S1"),
        SearchResult(title="T2", url="https://n.example/1", snippet="S2"),
    ])
    llm = FakeLLM(replies=[GOOD_JSON])
    card = build_intro(REC, search, llm)
    assert card.kind == "full"
    assert card.sources == ["https://openrouter.ai/vendor/model-a:free", "https://n.example/1"]
