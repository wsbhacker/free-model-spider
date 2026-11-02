"""v3 确认管线：按通道优先级解析单个模型的发布日（spec §4，纯函数层）。

优先级：manual > 公告源文章页提取（官方）> models.dev 策展日 > OpenRouter 上架日。
铁律：提取失败与公告源不可用都是**暂态**（unconfirmed 次日重试），绝不因暂态
故障降级目录通道——confirmed 永固，降级不可逆；只有公告列表"结构性无匹配条目"
（抓取成功且条目集合不含该模型）才落目录日期（†）。
"""

import httpx

from free_model_spider.core.vendor_config import ModelConfig, VendorConfig
from free_model_spider.core.vendor_judge import Judgment, extract_from_article
from free_model_spider.core.vendor_source import AnnouncementEntry
from free_model_spider.core.vendor_stats import (
    DirectoryRecord,
    ModelState,
    is_variant_record,
    model_matches,
    valid_directory_date,
)
from free_model_spider.llm.client import LLMClient


def _confirmed(model: ModelConfig, day: str, url: str | None, source: str,
               reason: str, today: str) -> ModelState:
    return ModelState(
        key=model.key, name=model.name, date=day, url=url, source=source,
        status="confirmed", reason=reason, last_verified=today,
    )


def _unconfirmed(model: ModelConfig, reason: str) -> ModelState:
    return ModelState(key=model.key, name=model.name, status="unconfirmed", reason=reason)


def _best_directory_record(
    model: ModelConfig, records: list[DirectoryRecord], today: str
) -> DirectoryRecord | None:
    """目录通道选条目：窄匹配命中 → 剔除变体与非法日期 → modelsdev 优先 → 日期最新。"""
    matched = [r for r in records if model_matches(model, r.id.casefold())]
    usable = [
        r for r in matched
        if not is_variant_record(r) and valid_directory_date(r.date, today)
    ]
    if not usable:
        return None
    modelsdev = [r for r in usable if r.origin == "modelsdev"]
    return max(modelsdev or usable, key=lambda r: r.date or "")


def resolve_model(
    vendor: VendorConfig,
    model: ModelConfig,
    listings: list[AnnouncementEntry] | None,
    dir_records: list[DirectoryRecord],
    llm: LLMClient | None,
    client: httpx.Client,
    today: str,
) -> ModelState:
    """解析单个模型（spec §4）。listings=None 表示公告源当日不可用（暂态）。"""
    if model.manual is not None:
        return _confirmed(model, model.manual.date, model.manual.url, "manual",
                          "manual 纠偏直接采用", today)

    if vendor.announcements:
        if listings is None:
            return _unconfirmed(model, "公告源不可用（暂态，次日重试）")
        entries = [
            entry for entry in listings
            if entry.date and model_matches(model, entry.title.casefold())
        ]
        if entries:
            if llm is None:
                return _unconfirmed(model, "LLM 未配置（暂态，次日重试）")
            newest = max(entries, key=lambda entry: entry.date)  # 平局取文档序在前
            judgment: Judgment | None = extract_from_article(
                llm, client, vendor, model.name, newest.url)
            if judgment is None:
                return _unconfirmed(model, "公告提取/校验失败（暂态，次日重试）")
            return _confirmed(
                model, judgment.release_date, judgment.source_urls[0], "official",
                f"公告页提取（{newest.title[:40]}）", today)
        # 命中零条目：结构性缺席 → 落目录通道（不 return）

    record = _best_directory_record(model, dir_records, today)
    if record is None:
        return _unconfirmed(model, "公告与目录均无可用日期")
    return _confirmed(model, record.date or "", None, "directory",
                      f"目录日期（{record.origin}）", today)
