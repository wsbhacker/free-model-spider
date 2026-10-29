"""厂商大模型新鲜度统计的基线快照模型、读写与天数计算（纯函数层）。"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from free_model_spider.core.vendor_config import (
    VendorConfig,
    VendorReleasesConfig,
)
from free_model_spider.net import request_with_retries


class LineState(BaseModel):
    """单个厂商单条产品线的新鲜度状态。"""

    key: str
    model: str | None = None
    release_date: str | None = None
    url: str | None = None
    last_verified: str | None = None
    status: Literal["ok", "pending", "stale-baseline"] = "pending"
    reason: str = ""
    pending_days: int = 0


class VendorState(BaseModel):
    """单个厂商的状态：已见目录 id 集合 + 各产品线状态。"""

    id: str
    seen_directory_ids: list[str] = Field(default_factory=list)
    lines: list[LineState] = Field(default_factory=list)


class Baseline(BaseModel):
    """某一天的基线快照：所有厂商的全量状态。"""

    date: str
    vendors: list[VendorState] = Field(default_factory=list)


def baseline_path(data_dir: Path, date: str) -> Path:
    return data_dir / "vendor-releases" / f"{date}.json"


def find_latest_baseline(data_dir: Path, on_or_before: str) -> Path | None:
    """选 stem <= on_or_before 的最大日期基线；同日重跑可命中当日快照。"""
    directory = data_dir / "vendor-releases"
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
    """按配置生成全 pending 的空基线：每厂商每线一条、已见目录为空。"""
    return Baseline(
        date=date,
        vendors=[
            VendorState(
                id=vendor.id,
                seen_directory_ids=[],
                lines=[LineState(key=line.key) for line in vendor.lines],
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
# 目录采集与事件检测（Task 3）
# --------------------------------------------------------------------------

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_API_URL = "https://models.dev/api.json"

_EPOCH_DATE = "1970-01-01"  # release_date/last_verified 全缺时的兜底参考日


def collect_directory_entries(
    client: httpx.Client, config: VendorReleasesConfig
) -> tuple[dict[str, list[str]], list[str]]:
    """抓取 OpenRouter 与 models.dev 全量目录，按 directory_slugs 归属到厂商。

    返回 (vendor_id -> 条目列表, 错误列表)。条目 id：OpenRouter 用完整模型 id
    （如 ``z-ai/glm-5.3``），models.dev 归一为 ``f"{provider}:{model_id}"``。
    单家失败降级为跳过该家并记 error；两家全失败返回 ``({}, [两条error])``。
    """
    openrouter_ids: list[str] = []
    errors: list[str] = []
    try:
        resp = request_with_retries(client, "GET", OPENROUTER_MODELS_URL)
        for item in resp.json()["data"]:
            model_id = item.get("id")
            if model_id:
                openrouter_ids.append(model_id)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"openrouter: {exc}")

    modelsdev_entries: list[tuple[str, str]] = []
    try:
        resp = request_with_retries(client, "GET", MODELS_DEV_API_URL)
        payload = resp.json()
        for provider, node in payload.items():
            if not isinstance(node, dict):
                continue
            for model_id in node.get("models") or {}:
                modelsdev_entries.append((provider, f"{provider}:{model_id}"))
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"models.dev: {exc}")

    entries: dict[str, list[str]] = {}
    for vendor in config.vendors:
        openrouter_slugs = set(vendor.directory_slugs.get("openrouter", []))
        modelsdev_providers = set(vendor.directory_slugs.get("modelsdev", []))
        merged: list[str] = []
        seen: set[str] = set()
        for model_id in openrouter_ids:
            if model_id not in seen and model_id.split("/")[0] in openrouter_slugs:
                seen.add(model_id)
                merged.append(model_id)
        for provider, entry_id in modelsdev_entries:
            if entry_id not in seen and provider in modelsdev_providers:
                seen.add(entry_id)
                merged.append(entry_id)
        entries[vendor.id] = merged

    if len(errors) == 2:
        return {}, errors
    return entries, errors


def screen_new_entries(
    vendor: VendorConfig, state: VendorState, entries: list[str]
) -> list[str]:
    """目录条目粗筛：casefold 后 match 任一 regex 命中、不被任一 exclude 子串挡、
    且不在 seen_directory_ids 中；返回 casefold 后的新条目 id。

    有 override 的线不参与（其 match/exclude 不进入筛选用集合）。
    """
    active = [line for line in vendor.lines if line.override is None]
    match_patterns = [pattern for line in active for pattern in line.match]
    excludes = [word.casefold() for line in active for word in line.exclude]
    seen = {entry.casefold() for entry in state.seen_directory_ids}
    result: list[str] = []
    picked: set[str] = set()
    for entry in entries:
        folded = entry.casefold()
        if folded in seen or folded in picked:
            continue
        if not any(re.search(pattern, folded) for pattern in match_patterns):
            continue
        if any(word in folded for word in excludes):
            continue
        picked.add(folded)
        result.append(folded)
    return result


def _line_reference(line: LineState) -> str:
    """该线的新鲜度参考日：release_date 与 last_verified 取较大者，全缺用纪元日。"""
    refs = [d for d in (line.release_date, line.last_verified) if d]
    return max(refs) if refs else _EPOCH_DATE


def _line_is_stale(line: LineState, today: str, stale_days: int) -> bool:
    age = (date.fromisoformat(today) - date.fromisoformat(_line_reference(line))).days
    return age > stale_days


def detect_review(
    config: VendorReleasesConfig,
    baseline: Baseline,
    entries_by_vendor: dict[str, list[str]],
    today: str,
) -> dict[str, list[str]]:
    """事件检测：返回 vendor_id -> 触发原因列表（'new-entry' / 'stale'）。

    有粗筛后的新目录条目 → new-entry；无 override 的厂商其全部线满足
    ``today - max(release_date, last_verified 或 '1970-01-01') > stale_days``
    → stale。entries_by_vendor 缺该厂商（目录全失败）时仅按 stale 判定。
    """
    states = {v.id: v for v in baseline.vendors}
    reasons: dict[str, list[str]] = {}
    for vendor in config.vendors:
        vendor_reasons: list[str] = []
        entries = entries_by_vendor.get(vendor.id, [])
        state = states.get(vendor.id)
        if state is not None and screen_new_entries(vendor, state, entries):
            vendor_reasons.append("new-entry")
        if state is not None and not any(
            line.override is not None for line in vendor.lines
        ) and all(
            _line_is_stale(line, today, config.stale_days) for line in state.lines
        ):
            vendor_reasons.append("stale")
        if vendor_reasons:
            reasons[vendor.id] = vendor_reasons
    return reasons
