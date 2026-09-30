import json
import httpx
import respx

from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import ModelRecord, sort_records
from free_model_spider.sources.openrouter import is_free, normalize

PAGE = json.load(open("tests/fixtures/openrouter_models_page.json"))


def test_is_free():
    assert is_free(PAGE["data"][0])
    assert not is_free(PAGE["data"][3])  # data[2] 是缺 id 的坏记录但定价免费；收费条目在 data[3]


def test_normalize():
    r = normalize(PAGE["data"][0])
    assert isinstance(r, ModelRecord)
    assert r.source == "openrouter"
    assert r.id == "qwen/qwen3.8-27b:free"
    assert r.context_length == 262144
    assert r.input_modalities == ["text", "image"]
    assert r.page_url == "https://openrouter.ai/qwen/qwen3.8-27b:free"
    assert "description" not in r.raw


def test_sort_records_case_insensitive():
    a = ModelRecord(source="s", id="B/x", name="B")
    b = ModelRecord(source="s", id="a/y", name="A")
    assert [r.id for r in sort_records([a, b])] == ["a/y", "B/x"]


@respx.mock
def test_fetch_free_models_filters():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    src = create_source("openrouter", httpx.Client())
    records = src.fetch_free_models()
    assert [r.id for r in records] == [
        "qwen/qwen3.8-27b:free",
        "stealth/space-bunny-alpha",
    ]
    assert len(src.last_errors) == 1  # 缺 id 的坏记录被跳过并记录（spec §11）
    assert src.display_name == "OpenRouter"
    assert "openrouter" in available_sources()
