import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from free_model_spider.core.vendor_config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_IDS = [
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "qwen",
    "moonshot",
    "zhipu",
    "minimax",
]

VALID_YAML = r"""
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai], modelsdev: [openai] }
    models:
      - key: gpt-6-1-terra
        name: GPT-6.1 Terra
        match: ['gpt[ -]?6\.1[ -]?terra']
        exclude: [pro]
      - key: gpt-6-1-mini
        name: GPT-6.1 mini
        match: ['gpt[ -]?6\.1[ -]?mini']
        manual: { date: "2026-10-01", url: "https://openai.com/gpt-6-1-mini" }
"""

EMPTY_MODELS_YAML = r"""
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai] }
    models: []
"""

DUPLICATE_KEY_YAML = r"""
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai] }
    models:
      - key: gpt-6-1-terra
        name: GPT-6.1 Terra
        match: ['gpt[ -]?6\.1[ -]?terra']
      - key: gpt-6-1-terra
        name: GPT-6.1 Terra 二
        match: ['gpt[ -]?6\.1[ -]?terra pro']
"""


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "vendor_releases.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_fields(tmp_path):
    config = load_config(write_yaml(tmp_path, VALID_YAML))
    assert [v.id for v in config.vendors] == ["openai"]
    openai = config.vendors[0]
    assert openai.name == "OpenAI"
    assert openai.official_domains == ["openai.com"]
    assert openai.directory_slugs == {
        "openrouter": ["openai"],
        "modelsdev": ["openai"],
    }
    terra, mini = openai.models
    assert (terra.key, terra.name) == ("gpt-6-1-terra", "GPT-6.1 Terra")
    assert terra.exclude == ["pro"]
    assert terra.manual is None
    assert mini.manual is not None
    assert (mini.manual.date, mini.manual.url) == (
        "2026-10-01", "https://openai.com/gpt-6-1-mini")


def test_empty_models_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, EMPTY_MODELS_YAML))


def test_duplicate_model_key_raises(tmp_path):
    with pytest.raises(ValidationError, match="key 重复"):
        load_config(write_yaml(tmp_path, DUPLICATE_KEY_YAML))


def test_legacy_line_fields_rejected(tmp_path):
    """v3 显式不兼容 v2：lines/stale_days 混入必须报错，而不是静默忽略。"""
    legacy = VALID_YAML.replace(
        "vendors:", "stale_days: 45\nvendors:", 1).replace(
        "    models:", "    lines:\n      - key: flagship\n        name: GPT\n"
        "        match: ['gpt']\n    models:", 1)
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, legacy))


def test_yaml_syntax_error_wrapped_as_value_error_with_path(tmp_path):
    path = write_yaml(tmp_path, "vendors: [unclosed\n")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_config(path)


def test_real_vendor_releases_yml_shape():
    config = load_config(REPO_ROOT / "data" / "vendor_releases.yml")
    assert [v.id for v in config.vendors] == EXPECTED_IDS
    total = 0
    for vendor in config.vendors:
        assert vendor.name
        assert vendor.official_domains
        assert vendor.directory_slugs
        assert vendor.models
        total += len(vendor.models)
        for model in vendor.models:
            # 窄匹配可编译；不含日期（v3 铁律：清单里不维护日期）
            for pattern in model.match:
                re.compile(pattern)
            assert model.manual is None
    assert total >= 15  # 8 家 × 至少 2 模型起步


def matches_to(model, text: str) -> bool:
    """模型窄匹配语义：match 任一 regex（re.search）命中，且 exclude 无子串命中。"""
    return any(re.search(p, text) for p in model.match) and not any(
        w in text for w in model.exclude)


def test_real_model_patterns_are_narrow_and_mutually_exclusive():
    """同厂商模型 match 必须互斥：任一目录 id/标题不得同时命中两个模型。"""
    config = load_config(REPO_ROOT / "data" / "vendor_releases.yml")
    samples = {
        "openai": ["openai/gpt-6.1-terra-pro", "openai/gpt-6.1-terra",
                   "openai/gpt-6.1-terra:batch"],
        "anthropic": ["anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5",
                      "Claude Opus 5.5 发布"],
        "zhipu": ["z-ai/glm-5.3", "z-ai/glm-5.3-flash", "z-ai/glm-5.3-flashx",
                  "z-ai/glm-5.3-prime"],
        "minimax": ["minimax/minimax-m3.1-flash", "minimax/minimax-m31f",
                    "minimax/minimax-m3"],
        "qwen": ["qwen/qwen3.8-max-prime", "qwen/qwen3.8-max-0902",
                 "qwen/qwen3.8-flash"],
    }
    for vendor in config.vendors:
        if vendor.id not in samples:
            continue
        for text in samples[vendor.id]:
            hits = [m.key for m in vendor.models if matches_to(m, text.casefold())]
            assert len(hits) <= 1, (vendor.id, text, hits, "窄匹配必须互斥")
