import httpx
import respx

from free_model_spider.core import vendor_judge
from free_model_spider.core.vendor_config import ModelConfig, VendorConfig
from free_model_spider.core.vendor_judge import (
    SYSTEM_PROMPT,
    extract_from_article,
    validate_judgment,
)
from free_model_spider.llm.client import LLMError

VENDOR = VendorConfig(
    id="zai",
    name="智谱 AI",
    official_domains=["z.ai", "chat.z.ai", "docs.z.ai", "bigmodel.cn"],
    directory_slugs={"openrouter": ["z-ai"], "modelsdev": ["zai"]},
    models=[ModelConfig(key="glm-5-4", name="GLM-5.4", match=[r"glm[ -]?5\.4"])],
)
MODEL_NAME = VENDOR.models[0].name
ARTICLE_URL = "https://z.ai/blog/glm-5-4"

GOOD_ARTICLE_JSON = (
    '{"model": "GLM-5.4", "release_date": "2026-10-20", '
    '"evidence": ["页面原文：GLM-5.4 于 2026-10-20 正式发布"], "reason": ""}'
)


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


# ---------- validate_judgment 八连 ----------


def test_validate_rejects_missing_evidence():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=[ARTICLE_URL], evidence=[], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_date_absent_from_evidence_text():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=[ARTICLE_URL], evidence=["GLM-5.4 正式发布"], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_non_whitelist_domain():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=["https://techcrunch.com/glm-5-4"],
        evidence=["GLM-5.4 于 2026-10-20 发布"], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_accepts_all_none_with_reason():
    j = vendor_judge.Judgment(
        model=None, release_date=None, source_urls=[], evidence=[],
        reason="页面原文未提及发布日期",
    )
    assert validate_judgment(j, VENDOR.official_domains) is True


def test_validate_accepts_official_url_with_evidence_date():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=[ARTICLE_URL], evidence=["GLM-5.4 于 2026-10-20 正式发布"], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is True


def test_validate_rejects_lookalike_subdomain():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=["https://evil-z.ai.com/blog"],
        evidence=["GLM-5.4 于 2026-10-20 发布"], reason="",
    )
    assert validate_judgment(j, ["z.ai"]) is False


def test_validate_rejects_malformed_date():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026/10/20",
        source_urls=[ARTICLE_URL], evidence=["日期是 2026/10/20"], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


def test_validate_rejects_model_without_date():
    j = vendor_judge.Judgment(
        model="GLM-5.4", release_date=None,
        source_urls=[ARTICLE_URL], evidence=["GLM-5.4 发布"], reason="",
    )
    assert validate_judgment(j, VENDOR.official_domains) is False


# ---------- validate 日期归一化比对（2026-10-30 anthropic/google 误杀修复） ----------


def test_validate_accepts_abbreviated_month_date_evidence():
    # anthropic 页面原文 "Published Aug 5, 2025"——LLM 归一为 ISO 后须过校验
    j = vendor_judge.Judgment(
        model="Claude Opus 4.1", release_date="2025-08-05",
        source_urls=["https://www.anthropic.com/news/claude-opus-4-1"],
        evidence=["Claude Opus 4.1 Published Aug 5, 2025"], reason="",
    )
    assert validate_judgment(j, ["anthropic.com"]) is True


def test_validate_accepts_full_month_and_day_first_date_evidence():
    # google changelog "October 13, 2026" 与英式 "13 October 2026"
    month_first = vendor_judge.Judgment(
        model="Gemini 4 Argon", release_date="2026-10-13",
        source_urls=["https://ai.google.dev/gemini-api/docs/changelog"],
        evidence=["October 13, 2026 Gemini 4 Argon generally available"], reason="",
    )
    assert validate_judgment(month_first, ["ai.google.dev"]) is True
    day_first = vendor_judge.Judgment(
        model="Gemini 4 Argon", release_date="2026-10-13",
        source_urls=["https://ai.google.dev/gemini-api/docs/changelog"],
        evidence=["13 October 2026 generally available"], reason="",
    )
    assert validate_judgment(day_first, ["ai.google.dev"]) is True


def test_validate_accepts_chinese_and_slash_date_evidence():
    chinese = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=[ARTICLE_URL], evidence=["GLM-5.4 于2026年10月20日发布"], reason="",
    )
    assert validate_judgment(chinese, VENDOR.official_domains) is True
    slash = vendor_judge.Judgment(
        model="GLM-5.4", release_date="2026-10-20",
        source_urls=[ARTICLE_URL], evidence=["发布于 2026/10/20"], reason="",
    )
    assert validate_judgment(slash, VENDOR.official_domains) is True


def test_validate_rejects_mismatched_normalized_date():
    # evidence 只有 Aug 5，LLM 却归一出 8 月 6 日 → 归一后对不上仍拒绝
    j = vendor_judge.Judgment(
        model="Claude Opus 4.1", release_date="2025-08-06",
        source_urls=["https://www.anthropic.com/news/claude-opus-4-1"],
        evidence=["Claude Opus 4.1 Published Aug 5, 2025"], reason="",
    )
    assert validate_judgment(j, ["anthropic.com"]) is False


# ---------- extract_from_article ----------


@respx.mock
def test_extract_from_article_returns_validated_judgment():
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(
            200, text="<html><script>var x=1;</script><body>"
                      "<p>智谱正式发布 GLM-5.4：于 2026-10-20 正式发布。</p></body></html>"
        )
    )
    llm = FakeLLM([GOOD_ARTICLE_JSON])
    judgment = extract_from_article(llm, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert judgment is not None
    assert judgment.model == "GLM-5.4"
    assert judgment.release_date == "2026-10-20"
    assert judgment.source_urls == [ARTICLE_URL]  # 回填为被抓取的公告页
    assert llm.calls[0][0] is SYSTEM_PROMPT  # 共用 prompt 基座
    assert "2026-10-20" in llm.calls[0][1]  # 页面原文进了 user prompt
    assert "目标模型：GLM-5.4" in llm.calls[0][1]  # 模型上下文进了 user prompt


def test_extract_from_article_no_llm_returns_none():
    assert extract_from_article(None, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL) is None


def test_extract_from_article_fetch_failure_logs_and_returns_none(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(vendor_judge, "request_with_retries", boom)
    out = extract_from_article(FakeLLM([GOOD_ARTICLE_JSON]), httpx.Client(),
                               VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is None
    assert "公告页抓取失败" in capsys.readouterr().out


@respx.mock
def test_extract_from_article_truncates_body_to_limit():
    tail_marker = "TAIL-MARKER-BEYOND-LIMIT"
    body = "GLM-5.4 于 2026-10-20 正式发布。" + "x" * 13000 + tail_marker
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, text=body))
    llm = FakeLLM([GOOD_ARTICLE_JSON])
    out = extract_from_article(llm, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is not None
    user_prompt = llm.calls[0][1]
    assert "2026-10-20" in user_prompt
    assert tail_marker not in user_prompt


@respx.mock
def test_extract_from_article_two_bad_json_degrades_with_log(capsys):
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, text="内容"))
    llm = FakeLLM(["not json at all", '{"model": broken'])
    out = extract_from_article(llm, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is None
    out_text = capsys.readouterr().out
    assert "两次均非 JSON" in out_text
    assert "extract zai/GLM-5.4" in out_text


@respx.mock
def test_extract_from_article_llm_error_logs_cause(capsys):
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, text="内容"))
    llm = FakeLLM([LLMError("所有模型均不可用：m/a:free(HTTPStatusError: 429)")])
    out = extract_from_article(llm, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is None
    out_text = capsys.readouterr().out
    assert "LLM 调用失败" in out_text
    assert "HTTPStatusError: 429" in out_text


@respx.mock
def test_extract_from_article_missing_date_logs_reason(capsys):
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(200, text="本页介绍 GLM-5.4 能力，未写日期。"))
    llm = FakeLLM([
        '{"model": "GLM-5.4", "release_date": null, "evidence": [], '
        '"reason": "页面原文未出现发布日期"}'
    ])
    out = extract_from_article(llm, httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is None
    out_text = capsys.readouterr().out
    assert "公告页未含模型/日期" in out_text
    assert "页面原文未出现发布日期" in out_text


@respx.mock
def test_extract_from_article_validation_failure_logs_detail(capsys):
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, text="内容"))
    bad = (
        '{"model": "GLM-5.4", "release_date": "2026-10-20", '
        '"evidence": ["与日期无关的句子"], "reason": ""}'
    )
    out = extract_from_article(FakeLLM([bad]), httpx.Client(), VENDOR, MODEL_NAME, ARTICLE_URL)
    assert out is None
    assert "提取未过校验" in capsys.readouterr().out

