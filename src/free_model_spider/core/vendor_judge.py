"""LLM 裁决管线：防捏造 prompt 基座、条目归类、官方文章页提取（v2，spec §4）。

所有 LLM 调用共用 SYSTEM_PROMPT（v1 spec §7.1 五条硬约束），程序侧
validate_judgment 兜底校验 evidence 与官方域白名单——不信任提示词，
校验不过一律降级 None（pending）。

v2 变更：Brave 搜索链路移除。日期真值 = 官网公告文章页原文，
extract_from_article 抓页面正文一次性提取 model/release_date。
"""

import json
import re
from html import unescape
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from free_model_spider.core.vendor_config import LineConfig, VendorConfig
from free_model_spider.llm.client import LLMClient, LLMError
from free_model_spider.net import request_with_retries

SYSTEM_PROMPT = (
    "你是大模型发布信息核查员，任务是从给定材料中提取可验证的事实。"
    "以下硬约束逐条强制，违反任何一条即为无效输出：\n"
    "1. 只准依据用户消息中给定的材料（搜索结果、页面原文、目录条目）。"
    "材料里没写的信息一律视为不存在，禁止用你训练记忆中的模型发布知识补全、校对或回忆。\n"
    "2. 禁止推算与估算：日期只允许原样转录材料中出现的日期并归一化为 YYYY-MM-DD；"
    "“大约”“最近”“一个月前”这类模糊表述不接受，宁可输出 null。\n"
    "3. 每个事实字段必须给出处：任何非 null 字段都必须在 evidence 中附材料原文摘句或来源 URL；"
    "URL 证据必须是材料中出现的原始链接且属于指定官方域白名单，禁止自行拼凑或猜测 URL；"
    "给不出 evidence 的字段强制置 null 并在 reason 写明缺什么。\n"
    "4. 不确定就是不确定：材料不足或存疑时输出 null 加 reason，"
    "绝不猜测、绝不编造、绝不“合理推断”。\n"
    "5. 只输出用户消息所约定 schema 的单个 JSON 对象，不输出任何多余文字。"
)

ARTICLE_TEXT_LIMIT = 12000  # 文章页正文截断（导航噪声在前，日期通常在首个内容屏）
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_CATEGORIES = {"flagship", "sub", "ignore"}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class Judgment(BaseModel):
    """单条产品线的 LLM 裁决结果。"""

    model: str | None = None
    release_date: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""


def _host_in_whitelist(url: str, official_domains: list[str]) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    host = host.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in official_domains)


def validate_judgment(j: Judgment, official_domains: list[str]) -> bool:
    """程序侧兜底校验：有事实就必须有官方域出处与含日期的 evidence。

    model/release_date 任一非 None 时：两者须均非 None、evidence 非空、
    release_date（YYYY-MM-DD 归一后）出现在 evidence/source_urls 文本中、
    source_urls 非空且全部 host 落在官方域白名单内。两者均 None（材料不足）放行。
    """
    if j.model is None and j.release_date is None:
        return True
    if j.model is None or j.release_date is None:
        return False
    if not j.evidence or not j.source_urls:
        return False
    if not DATE_RE.match(j.release_date):
        return False
    evidence_text = "\n".join([*j.evidence, *j.source_urls])
    if j.release_date not in evidence_text:
        return False
    return all(_host_in_whitelist(u, official_domains) for u in j.source_urls)


def _parse_llm_json(text: str) -> dict | None:
    """提取首个 {...} 子串并 json.loads，容错 ```json 围栏；失败返回 None。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _complete_json(llm: LLMClient, user_prompt: str, context: str = "") -> dict | None:
    """两次循环模式（沿用 intro.py 的 build_intro）：坏 JSON 重试 1 次，LLMError 直接终止。

    失败时打印一行诊断（LLM 失败原因或原始输出片段），供 Actions 日志定位
    降级环节；程序侧仍由调用方降级，不抛出。
    """
    where = f"（{context}）" if context else ""
    for _ in range(2):
        try:
            text = llm.complete(SYSTEM_PROMPT, user_prompt)
        except LLMError as exc:
            print(f"vendors: LLM 调用失败{where}：{exc}")
            return None
        parsed = _parse_llm_json(text)
        if parsed is not None:
            return parsed
    print(f"vendors: LLM 输出两次均非 JSON{where}：{text[:160]!r}")
    return None


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clean_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _lines_block(vendor: VendorConfig) -> str:
    return "\n".join(
        f"- {line.key}：{line.name}（匹配正则：{line.match}，排除：{line.exclude or '无'}）"
        for line in vendor.lines
    )


def _classify_user_prompt(vendor: VendorConfig, entry_title: str) -> str:
    return (
        f"厂商：{vendor.name}\n"
        f"产品线定义：\n{_lines_block(vendor)}\n"
        f"官方公告标题：{entry_title}\n"
        "任务：只依据以上材料判断该公告归属哪条产品线；"
        "ignore 表示非模型发布公告（活动/政策/研究/招聘等）或衍生专用模型（不属于任何线）。\n"
        '只输出一个 JSON 对象：{"category": "flagship|sub|ignore", "reason": "一句话理由"}'
    )


def _article_user_prompt(
    vendor: VendorConfig, line: LineConfig, url: str, page_text: str
) -> str:
    domains = "、".join(vendor.official_domains)
    return (
        f"厂商：{vendor.name}（官方域白名单：{domains}）\n"
        f"目标产品线：{line.name}（key={line.key}）\n"
        f"官方公告页原文（URL：{url}）：\n{page_text}\n"
        "任务：只依据以上页面原文，提取该公告发布的模型名称与发布日期（YYYY-MM-DD）。"
        "页面原文里没有的日期一律输出 null，禁止从 URL 或模型名推算。\n"
        '只输出一个 JSON 对象：{"model": "模型名或null", "release_date": "YYYY-MM-DD或null", '
        '"evidence": ["页面原文摘句"], "reason": "页面未包含时说明"}'
    )


def classify_entry(llm: LLMClient | None, vendor: VendorConfig, entry_title: str) -> tuple[str, str]:
    """公告条目归类：返回 ('flagship'|'sub'|'ignore', reason)；不可用/失败降级 ('pending', '')。"""
    if llm is None:
        return "pending", ""
    parsed = _complete_json(
        llm, _classify_user_prompt(vendor, entry_title),
        context=f"classify {vendor.id}/{entry_title[:60]}",
    )
    if parsed is None:
        return "pending", ""
    category = _clean_str(parsed.get("category"))
    if category is None or category.lower() not in _VALID_CATEGORIES:
        print(f"vendors: {vendor.id} 公告归类输出非法类别：{category!r}")
        return "pending", ""
    return category.lower(), str(parsed.get("reason") or "")


def strip_html(html: str) -> str:
    """剥 script/style/noscript 与全部标签，反转义并折叠空白（列表/文章共用）。"""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", unescape(cleaned)).strip()


def extract_from_article(
    llm: LLMClient | None,
    client: httpx.Client,
    vendor: VendorConfig,
    line: LineConfig,
    url: str,
) -> Judgment | None:
    """抓官方公告文章页原文，LLM 一次性提取 model/release_date 并校验。

    任何失败（网络、LLM、解析、无日期、validate 不过）→ None，保留旧基线转 pending。
    source_urls 固定回填被抓取的公告页 URL（官方域白名单已在 validate 兜底）。
    """
    if llm is None:
        return None
    try:
        resp = request_with_retries(client, "GET", url, follow_redirects=True)
    except Exception as exc:
        print(f"vendors: {vendor.id}/{line.key} 公告页抓取失败 {url}：{exc!r}")
        return None
    parsed = _complete_json(
        llm,
        _article_user_prompt(vendor, line, url, strip_html(resp.text)[:ARTICLE_TEXT_LIMIT]),
        context=f"extract {vendor.id}/{line.key}",
    )
    if parsed is None:
        return None
    judgment = Judgment(
        model=_clean_str(parsed.get("model")),
        release_date=_clean_str(parsed.get("release_date")),
        evidence=_clean_str_list(parsed.get("evidence")),
        source_urls=[url],
        reason=str(parsed.get("reason") or ""),
    )
    if judgment.model is None or judgment.release_date is None:
        print(f"vendors: {vendor.id}/{line.key} 公告页未含模型/日期 {url}："
              f"{judgment.reason or '字段为 null'}")
        return None
    if not validate_judgment(judgment, vendor.official_domains):
        print(f"vendors: {vendor.id}/{line.key} 提取未过校验 {url}："
              f"model={judgment.model} date={judgment.release_date} "
              f"evidence={judgment.evidence}")
        return None
    return judgment
