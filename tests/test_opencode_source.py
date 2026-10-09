import json
import re

import httpx
import respx

from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import ModelRecord
from free_model_spider.sources.opencode import (
    DOCS_URL,
    MODELS_DEV_URL,
    is_free,
    normalize,
    parse_tables,
)

HTML = open("tests/fixtures/opencode_docs_zen.html", encoding="utf-8").read()
MODELSDEV = json.load(open("tests/fixtures/modelsdev_opencode.json"))


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
    r = normalize("Big Pickle", name_to_id["Big Pickle"], row, {
        "context_length": 200000,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
    })
    assert isinstance(r, ModelRecord)
    assert r.source == "opencode"
    assert r.id == "big-pickle"
    assert r.name == "Big Pickle"
    assert r.context_length == 200000  # models.dev 补全
    assert r.input_modalities == ["text"]
    assert r.page_url == DOCS_URL
    assert r.raw["pricing"]["cached_read"] == "Free"


@respx.mock
def test_fetch_free_models_filters():
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=HTML))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=MODELSDEV)
    )
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    by_id = {r.id: r for r in records}
    assert [r.id for r in records] == [
        "big-pickle",      # 免费且在架
        "exo-free",        # 免费（输出列带空白，已归一化）
        "mimo-v2.5-free",  # 免费
    ]  # Old Codex 免费但已弃用 → 剔除；Half Free 只有一侧免费 → 剔除；GPT 6 Astra 付费 → 剔除
    assert by_id["big-pickle"].context_length == 200000
    assert by_id["big-pickle"].input_modalities == ["text"]
    assert by_id["exo-free"].context_length == 1048576
    assert by_id["exo-free"].input_modalities == ["text", "image"]
    assert by_id["mimo-v2.5-free"].context_length is None  # models.dev 未收录 → 静默留空
    assert by_id["mimo-v2.5-free"].input_modalities == []
    assert len(src.last_errors) == 1  # Ghost Free 在模型表无对应行；JOIN 未命中不算异常
    assert "Ghost Free" in src.last_errors[0]
    assert src.display_name == "OpenCode"
    assert "opencode" in available_sources()


@respx.mock
def test_fetch_modelsdev_failure_degrades(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=HTML))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(500))
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    assert [r.id for r in records] == [  # 第三方故障不阻断官网免费列表
        "big-pickle", "exo-free", "mimo-v2.5-free",
    ]
    assert all(r.context_length is None for r in records)  # 字段退回未知
    assert any("元数据" in e for e in src.last_errors)
    assert any("Ghost Free" in e for e in src.last_errors)


@respx.mock
def test_fetch_modelsdev_missing_provider():
    respx.get(DOCS_URL).mock(return_value=httpx.Response(200, text=HTML))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, json={}))
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    assert len(records) == 3
    assert all(r.context_length is None for r in records)
    assert any("models.dev" in e for e in src.last_errors)


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
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=MODELSDEV)
    )
    src = create_source("opencode", httpx.Client())
    records = src.fetch_free_models()
    # 弃用表缺失视为空集合：Old Codex 不再被剔除，其余行为不变
    assert [r.id for r in records] == [
        "big-pickle", "exo-free", "mimo-v2.5-free", "old-codex",
    ]
    assert len(src.last_errors) == 1  # 仅 Ghost Free 的 JOIN 失败
