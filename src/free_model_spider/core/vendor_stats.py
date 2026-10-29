"""厂商大模型新鲜度统计的基线快照模型、读写与天数计算（纯函数层）。"""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from free_model_spider.core.vendor_config import VendorReleasesConfig


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
