import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from free_model_spider.llm.client import LLMClient, LLMError
from free_model_spider.search.base import SearchProvider, SearchResult
from free_model_spider.sources.base import ModelRecord

SHANGHAI = ZoneInfo("Asia/Shanghai")


class IntroCard(BaseModel):
    model_id: str
    kind: str  # "full" | "metadata"
    title: str
    summary: str = ""
    highlights: list[str] = Field(default_factory=list)
    caveat: str = ""
    sources: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = (
    "你是大模型资讯编辑。请只基于给定材料（平台元数据与搜索结果摘要）整理该模型的中文介绍，"
    "禁止编造材料之外的信息；材料不足时在 caveat 字段说明。只输出一个 JSON 对象："
    '{"summary": "80-150字介绍", "highlights": ["要点1", "要点2"], "caveat": "信息局限或空字符串"}'
)


def metadata_card(record: ModelRecord) -> IntroCard:
    created = (
        datetime.fromtimestamp(record.created, tz=SHANGHAI).strftime("%Y-%m-%d")
        if record.created
        else "未知"
    )
    vendor = record.id.split("/")[0]
    summary = f"{vendor} 旗下模型，上下文窗口 {record.context_length or '未知'} tokens。"
    if record.description:
        excerpt = record.description[:200] + ("…" if len(record.description) > 200 else "")
        summary += f" 平台描述摘录：{excerpt}"
    return IntroCard(
        model_id=record.id,
        kind="metadata",
        title=record.name,
        summary=summary,
        highlights=[
            f"输入模态：{'、'.join(record.input_modalities) or '未知'}",
            f"输出模态：{'、'.join(record.output_modalities) or '未知'}",
            f"上架时间：{created}",
        ],
        caveat="未经过联网搜索与 LLM 整理，内容为平台元数据拼装。",
        sources=[record.page_url] if record.page_url else [],
    )


def _user_prompt(record: ModelRecord, results: list[SearchResult]) -> str:
    meta = record.model_dump_json(
        include={
            "id", "name", "context_length", "input_modalities",
            "output_modalities", "created", "description",
        }
    )
    lines = [f"平台元数据：{meta}", "搜索结果："]
    if results:
        lines += [f"- {r.title} | {r.url} | {r.snippet}" for r in results]
    else:
        lines.append("-（无，搜索不可用或未配置）")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        return None
    return data


def build_intro(
    record: ModelRecord,
    search: SearchProvider | None,
    llm: LLMClient | None,
) -> IntroCard:
    results: list[SearchResult] = []
    if search is not None:
        query = f"{record.name} {record.id.split('/')[0]}"
        try:
            results = search.search(query)
        except Exception:
            results = []

    card: IntroCard
    if llm is not None:
        parsed: dict | None = None
        for _ in range(2):
            try:
                text = llm.complete(_SYSTEM_PROMPT, _user_prompt(record, results))
            except LLMError:
                break
            parsed = _parse_llm_json(text)
            if parsed is not None:
                break
        if parsed is not None:
            source_urls = [record.page_url] if record.page_url else []
            source_urls += [r.url for r in results if r.url and r.url != record.page_url]
            source_urls = list(dict.fromkeys(source_urls))[:4]
            card = IntroCard(
                model_id=record.id,
                kind="full",
                title=record.name,
                summary=parsed["summary"],
                highlights=[str(h) for h in (parsed.get("highlights") or [])][:5],
                caveat=str(parsed.get("caveat") or ""),
                sources=source_urls,
            )
            return card

    card = metadata_card(record)
    return card
