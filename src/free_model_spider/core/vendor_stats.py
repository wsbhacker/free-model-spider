"""厂商新鲜度统计 v3：模型级状态、基线读写与目录双源采集（纯函数层）。

v3 变更（spec 2026-11-01）：发布日是不可变事实，confirmed 一次永固——
v2 的 pending 计数/冻结/重臂状态机整体删除；目录条目携带各自源的日期值
（models.dev 策展发布日 / OpenRouter 上架日），作为公告源缺席时的日期通道。
"""

import json
import re
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NamedTuple

import httpx
from pydantic import BaseModel, Field

from free_model_spider.core.vendor_config import (
    ModelConfig,
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.net import request_with_retries


class ModelState(BaseModel):
    """单个已维护模型的确认状态（v3 spec §6）。confirmed 后永不再动。"""

    key: str
    name: str
    date: str | None = None
    url: str | None = None
    source: Literal["official", "directory", "manual"] | None = None
    status: Literal["confirmed", "unconfirmed"] = "unconfirmed"
    reason: str = ""
    last_verified: str | None = None


class VendorState(BaseModel):
    """单个厂商的状态：已见目录 id 集合（发现信号去重）+ 各模型确认状态。"""

    id: str
    seen_directory_ids: list[str] = Field(default_factory=list)
    models: list[ModelState] = Field(default_factory=list)


class Baseline(BaseModel):
    """某一天的基线快照（data/vendor-models/，与 v2 的 vendor-releases/ 彻底分目录）。"""

    schema_version: Literal[1] = 1
    date: str
    vendors: list[VendorState] = Field(default_factory=list)


def baseline_path(data_dir: Path, date: str) -> Path:
    return data_dir / "vendor-models" / f"{date}.json"


def find_latest_baseline(data_dir: Path, on_or_before: str) -> Path | None:
    """选 stem <= on_or_before 的最大日期基线；同日重跑可命中当日快照。"""
    directory = data_dir / "vendor-models"
    if not directory.is_dir():
        return None
    candidates = sorted(p for p in directory.glob("*.json") if p.stem <= on_or_before)
    return candidates[-1] if candidates else None


def save_baseline(b: Baseline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(b.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> Baseline:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Baseline.model_validate(data)


def empty_baseline(date: str, config: VendorReleasesConfig) -> Baseline:
    """按配置生成全 unconfirmed 的空基线：每厂商每模型一条、已见目录为空。"""
    return Baseline(
        date=date,
        vendors=[
            VendorState(
                id=vendor.id,
                seen_directory_ids=[],
                models=[
                    ModelState(key=model.key, name=model.name)
                    for model in vendor.models
                ],
            )
            for vendor in config.vendors
        ],
    )


def days_since(release_date: str, today: str) -> int:
    """纯 ISO 日期字符串相减；release_date 晚于 today 视为非法数据。"""
    released = date.fromisoformat(release_date)
    current = date.fromisoformat(today)
    if released > current:
        raise ValueError(
            f"release_date {release_date} 晚于 today {today}，疑似非法数据"
        )
    return (current - released).days


# --------------------------------------------------------------------------
# 模型窄匹配与未维护新模型发现（spec §3/§5）
# --------------------------------------------------------------------------


def model_matches(model: ModelConfig, text: str) -> bool:
    """单模型窄匹配：match 任一 regex 命中且不被本模型 exclude 子串挡（text 需已 casefold）。"""
    if any(re.search(pattern, text) for pattern in model.match):
        return not any(word.casefold() in text for word in model.exclude)
    return False


def discover_unmaintained(
    vendor: VendorConfig, state: VendorState, records: list["DirectoryRecord"]
) -> list[str]:
    """目录新条目中不匹配任何已维护模型、且不在 seen 的（发现信号，spec §5）。

    返回原始 id（进告警文案）；cli 侧负责把全部当日条目入 seen（含命中的）。
    """
    seen = {entry.casefold() for entry in state.seen_directory_ids}
    result: list[str] = []
    picked: set[str] = set()
    for record in records:
        folded = record.id.casefold()
        if folded in seen or folded in picked:
            continue
        if any(model_matches(model, folded) for model in vendor.models):
            continue
        picked.add(folded)
        result.append(record.id)
    return result


# --------------------------------------------------------------------------
# 目录双源采集（v3：条目携带各自源日期；openrouter=上架日、modelsdev=策展发布日）
# --------------------------------------------------------------------------

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_API_URL = "https://models.dev/api.json"

DirectoryOrigin = Literal["openrouter", "modelsdev"]


class DirectoryRecord(NamedTuple):
    """单条目录条目：id（openrouter 为 ``provider/model``，modelsdev 为 ``provider:model``）
    与该源给出的日期（可能为 None）。
    """

    id: str
    date: str | None
    origin: DirectoryOrigin


def collect_directory_entries(
    client: httpx.Client, config: VendorReleasesConfig
) -> tuple[dict[str, list[DirectoryRecord]], list[str]]:
    """抓取 OpenRouter 与 models.dev 全量目录，按 directory_slugs 归属到厂商。

    返回 (vendor_id -> 条目列表, 错误列表)。单家失败降级为跳过该家并记 error；
    两家全失败返回 ``({}, [两条error])``。
    """
    openrouter_items: list[tuple[str, str | None]] = []  # (id, created→UTC 日期)
    errors: list[str] = []
    try:
        resp = request_with_retries(client, "GET", OPENROUTER_MODELS_URL)
        for item in resp.json()["data"]:
            model_id, created = item.get("id"), item.get("created")
            if not model_id:
                continue
            day = (
                datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")
                if created
                else None
            )
            openrouter_items.append((model_id, day))
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"openrouter: {exc}")

    modelsdev_items: list[tuple[str, str | None]] = []  # (provider:model, release_date)
    try:
        resp = request_with_retries(client, "GET", MODELS_DEV_API_URL)
        payload = resp.json()
        for provider, node in payload.items():
            if not isinstance(node, dict):
                continue
            for model_id, info in (node.get("models") or {}).items():
                release_date = info.get("release_date") if isinstance(info, dict) else None
                modelsdev_items.append((f"{provider}:{model_id}", release_date))
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"models.dev: {exc}")

    entries: dict[str, list[DirectoryRecord]] = {}
    for vendor in config.vendors:
        openrouter_slugs = set(vendor.directory_slugs.get("openrouter", []))
        modelsdev_providers = set(vendor.directory_slugs.get("modelsdev", []))
        merged: list[DirectoryRecord] = []
        for model_id, day in openrouter_items:
            if model_id.split("/")[0] in openrouter_slugs:
                merged.append(DirectoryRecord(model_id, day, "openrouter"))
        for entry_id, release_date in modelsdev_items:
            if entry_id.split(":", 1)[0] in modelsdev_providers:
                merged.append(DirectoryRecord(entry_id, release_date, "modelsdev"))
        entries[vendor.id] = merged

    if len(errors) == 2:
        return {}, errors
    return entries, errors


# --------------------------------------------------------------------------
# 目录日期防护（spec §4：models.dev 占位符/回填错、双源未来日期）
# --------------------------------------------------------------------------

PLACEHOLDER_DATES = {"2024-01-01"}  # models.dev 已知占位符（v2 spec §2 实测）
DATE_FLOOR = "2020-01-01"  # 早于此视为回填脏数据


def valid_directory_date(day: str | None, today: str) -> bool:
    """目录日期可用性：非空、非占位符、在 [2020-01-01, today] 区间（ISO 字典序比较）。"""
    if not day:
        return False
    return day not in PLACEHOLDER_DATES and DATE_FLOOR <= day <= today


def is_variant_record(record: DirectoryRecord) -> bool:
    """OpenRouter 平台变体（``provider/model:batch`` 等）：模型段含 ':'，不作家条目。"""
    if record.origin != "openrouter":
        return False
    _, _, model_part = record.id.partition("/")
    return ":" in model_part
