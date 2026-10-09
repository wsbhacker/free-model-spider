import re

import httpx
import respx

from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import ModelRecord
from free_model_spider.sources.opencode import DOCS_URL, is_free, normalize, parse_tables

HTML = open("tests/fixtures/opencode_docs_zen.html", encoding="utf-8").read()


def test_parse_tables():
    name_to_id, pricing, deprecated = parse_tables(HTML)
    assert name_to_id["Big Pickle"] == "big-pickle"
    assert len(name_to_id) == 5
    assert len(pricing) == 7
    assert {p["model"] for p in pricing} >= {"Big Pickle", "GPT 6 Astra", "Half Free"}
    assert deprecated == {"Old Codex", "GPT 5.2 Codex"}


def test_is_free():
    assert is_free({"input": "Free", "output": "Free"})
    assert is_free({"input": "Free", "output": " Free "})  # 空白已归一化
    assert not is_free({"input": "Free", "output": "$1.00"})  # 只有一侧免费不算
    assert not is_free({"input": "$1.50", "output": "$12.00"})


def test_normalize():
    name_to_id, pricing, _ = parse_tables(HTML)
    row = next(p for p in pricing if p["model"] == "Big Pickle")
    r = normalize("Big Pickle", name_to_id["Big Pickle"], row)
    assert isinstance(r, ModelRecord)
    assert r.source == "opencode"
    assert r.id == "big-pickle"
    assert r.name == "Big Pickle"
    assert r.context_length is None  # 官网表格不提供
    assert r.page_url == DOCS_URL
    assert r.raw["pricing"]["cached_read"] == "Free"


@respx.mock
def test_fetch_free_models_filters():
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=HTML))
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    assert [r.id for r in records] == [
        "big-pickle",      # 免费且在架
        "exo-free",        # 免费（输出列带空白，已归一化）
        "mimo-v2.5-free",  # 免费
    ]  # Old Codex 免费但已弃用 → 剔除；Half Free 只有一侧免费 → 剔除；GPT 6 Astra 付费 → 剔除
    assert len(src.last_errors) == 1  # Ghost Free 在模型表无对应行
    assert "Ghost Free" in src.last_errors[0]
    assert src.display_name == "OpenCode"
    assert "opencode" in available_sources()


@respx.mock
def test_fetch_without_pricing_table():
    broken = re.sub(r"<h2>Pricing</h2>\s*<table.*?</table>", "", HTML, flags=re.S)
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=broken))
    src = create_source("opencode", httpx.Client())
    assert src.fetch_free_models() == []
    assert any("定价表" in e for e in src.last_errors)


@respx.mock
def test_fetch_without_models_table():
    broken = re.sub(r"<h2>Models</h2>\s*<table.*?</table>", "", HTML, flags=re.S)
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=broken))
    src = create_source("opencode", httpx.Client())
    assert src.fetch_free_models() == []
    assert any("模型表" in e for e in src.last_errors)


@respx.mock
def test_fetch_without_deprecation_table_not_fatal():
    html_no_dep = re.sub(
        r"<h2>Deprecations</h2>\s*<table.*?</table>", "", HTML, flags=re.S
    )
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=html_no_dep))
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    # 弃用表缺失视为空集合：Old Codex 不再被剔除，其余行为不变
    assert [r.id for r in records] == [
        "big-pickle", "exo-free", "mimo-v2.5-free", "old-codex",
    ]
    assert len(src.last_errors) == 1  # 仅 Ghost Free 的 JOIN 失败
