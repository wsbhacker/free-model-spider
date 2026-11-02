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

VALID_YAML = """\
vendors:
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    aliases: [OpenAI]
"""


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "vendors.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_real_vendors_yml():
    """仓库真实 data/vendors.yml：8 家、id 顺序/集合、身份字段与别名齐全。"""
    config = load_config(REPO_ROOT / "data" / "vendors.yml")
    assert [v.id for v in config.vendors] == EXPECTED_IDS
    for vendor in config.vendors:
        assert vendor.name
        assert vendor.official_domains
    anthropic = next(v for v in config.vendors if v.id == "anthropic")
    assert anthropic.aliases == ["Claude"]
    moonshot = next(v for v in config.vendors if v.id == "moonshot")
    assert moonshot.official_domains == ["moonshot.cn", "moonshot.ai", "kimi.com"]


def test_legacy_fields_rejected(tmp_path):
    """v3 字段（models/announcements/directory_slugs/manual）混入必须报错，不静默忽略。"""
    legacy = VALID_YAML + (
        "    directory_slugs: { openrouter: [openai] }\n"
        "    models:\n"
        "      - key: gpt-6-1-terra\n"
        "        name: GPT-6.1 Terra\n"
        "        match: ['gpt']\n"
    )
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, legacy))


def test_duplicate_id_rejected(tmp_path):
    dup = VALID_YAML + (
        "  - id: openai\n"
        "    name: OpenAI 二号\n"
        "    official_domains: [openai.com]\n"
    )
    with pytest.raises(ValidationError, match="id 重复"):
        load_config(write_yaml(tmp_path, dup))


def test_invalid_id_rejected(tmp_path):
    bad = (
        "vendors:\n"
        "  - id: OpenAI\n"
        "    name: OpenAI\n"
        "    official_domains: [openai.com]\n"
    )
    with pytest.raises(ValidationError):
        load_config(write_yaml(tmp_path, bad))


def test_yaml_syntax_error_wrapped_as_value_error_with_path(tmp_path):
    path = write_yaml(tmp_path, "vendors: [unclosed\n")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_config(path)
