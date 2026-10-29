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
    "meta",
    "xai",
    "mistral",
    "cohere",
    "deepseek",
    "qwen",
    "moonshot",
    "zhipu",
    "minimax",
    "bytedance",
    "tencent",
    "stepfun",
]

VALID_YAML = r"""
stale_days: 30
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai], modelsdev: [openai] }
    lines:
      - key: flagship
        name: GPT 主线
        match: ['gpt-\d']
        exclude: [mini, nano]
      - key: sub
        name: GPT mini
        match: ['gpt-\d[\d.]*-?mini']
  - id: meta
    name: Meta
    official_domains: [ai.meta.com]
    directory_slugs: { openrouter: [meta-llama] }
    lines:
      - key: flagship
        name: Llama 主线
        match: ['llama-\d']
"""

FLAGSHIP_MISSING_YAML = r"""
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai] }
    lines:
      - key: sub
        name: GPT mini
        match: ['gpt-\d[\d.]*-?mini']
"""

DUPLICATE_KEY_YAML = r"""
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai] }
    lines:
      - key: flagship
        name: GPT 主线
        match: ['gpt-\d']
      - key: flagship
        name: GPT 主线 二
        match: ['gpt-\d']
"""


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "vendor_releases.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_fields(tmp_path):
    config = load_config(write_yaml(tmp_path, VALID_YAML))
    assert config.stale_days == 30
    assert [v.id for v in config.vendors] == ["openai", "meta"]
    openai = config.vendors[0]
    assert openai.name == "OpenAI"
    assert openai.official_domains == ["openai.com"]
    assert openai.directory_slugs == {
        "openrouter": ["openai"],
        "modelsdev": ["openai"],
    }
    assert [line.key for line in openai.lines] == ["flagship", "sub"]
    assert openai.lines[0].exclude == ["mini", "nano"]
    assert openai.lines[0].override is None


def test_stale_days_defaults_to_45(tmp_path):
    text = VALID_YAML.replace("stale_days: 30\n", "")
    config = load_config(write_yaml(tmp_path, text))
    assert config.stale_days == 45


def test_real_vendor_releases_yml_has_15_vendors():
    config = load_config(REPO_ROOT / "data" / "vendor_releases.yml")
    assert [v.id for v in config.vendors] == EXPECTED_IDS
    for vendor in config.vendors:
        assert vendor.name
        assert vendor.official_domains
        assert vendor.directory_slugs
        assert vendor.lines
        assert vendor.lines[0].key == "flagship"


def test_missing_flagship_line_raises(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, FLAGSHIP_MISSING_YAML))


def test_duplicate_line_key_raises(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, DUPLICATE_KEY_YAML))


def test_yaml_syntax_error_wrapped_as_value_error_with_path(tmp_path):
    path = write_yaml(tmp_path, "stale_days: 45\nvendors: [unclosed\n")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_config(path)


def screens_to(line, model_id: str) -> bool:
    """目录门铃粗筛语义：match 任一 regex（re.search）命中，且 exclude 无子串命中。"""
    return any(re.search(p, model_id) for p in line.match) and not any(
        w in model_id for w in line.exclude
    )


def test_real_flash_tier_ids_screen_to_sub_not_flagship():
    """实测模型 id 必须落入次旗舰线：flagship exclude 拦下、sub match 接住。"""
    config = load_config(REPO_ROOT / "data" / "vendor_releases.yml")
    vendors = {v.id: v for v in config.vendors}
    cases = [
        ("moonshot", "kimi-k3.1-pro-highspeed"),
        ("moonshot", "kimi-k2-flash"),
        ("deepseek", "deepseek-v4-flash"),
    ]
    for vendor_id, model_id in cases:
        lines = {line.key: line for line in vendors[vendor_id].lines}
        assert not screens_to(lines["flagship"], model_id), (
            vendor_id,
            model_id,
            "不应命中 flagship",
        )
        assert screens_to(lines["sub"], model_id), (
            vendor_id,
            model_id,
            "应命中 sub",
        )
