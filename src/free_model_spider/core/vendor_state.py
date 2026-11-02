"""厂商新鲜度 v4 状态模型：schema 4 基线读写与新鲜度窗口（纯函数层）。

v4 变更（spec 2026-11-02）：问询改走 Dify Agent，模型状态由 Agent 一次性回填——
release_date/url/added_on 必填，retired_on/superseded_by 显式记录生命周期。
状态目录统一为 ``data/vendor-releases/``；本模块自包含，不依赖 v3 配置模块。
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 无 active 模型时新鲜度窗口的回退宽度（today - WINDOW_FALLBACK_DAYS）
WINDOW_FALLBACK_DAYS = 180


class ModelState(BaseModel):
    """单个模型的生命周期状态：日期均为 ISO 字符串，由 Agent 回填。"""

    key: str
    name: str
    release_date: str
    url: str
    added_on: str
    retired_on: str | None = None
    superseded_by: str | None = None


class LastCheck(BaseModel):
    """单个厂商最近一次 Agent 问询结果。"""

    date: str
    status: Literal["ok", "transient"]
    detail: str = ""


class VendorState(BaseModel):
    """单个厂商的状态：模型清单 + 最近问询。"""

    id: str
    models: list[ModelState] = Field(default_factory=list)
    last_check: LastCheck | None = None

    def active_models(self) -> list[ModelState]:
        """未退役模型（retired_on is None），保持原序。"""
        return [model for model in self.models if model.retired_on is None]


class Baseline(BaseModel):
    """某一天的 v4 基线快照（data/vendor-releases/）。

    顶层 JSON 键为 ``"schema": 4``（by_alias 落盘）；字段名用 schema_version
    是因为 ``schema`` 会遮蔽 BaseModel.schema()。
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal[4] = Field(default=4, alias="schema")
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
        json.dumps(b.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> Baseline:
    """读取 v4 基线；schema != 4（含 v2/v3 旧文件）报错并提示 --bootstrap。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema") if isinstance(data, dict) else None
    if schema != 4:
        raise ValueError(
            f"{path}: 基线 schema={schema!r} 而非 4（形如旧 v2/v3 文件，不兼容）；"
            "请先运行 --bootstrap 生成当日 v4 基线"
        )
    return Baseline.model_validate(data)


def days_since(release_date: str, today: str) -> int:
    """纯 ISO 日期字符串相减；release_date 晚于 today 视为非法数据。"""
    released = date.fromisoformat(release_date)
    current = date.fromisoformat(today)
    if released > current:
        raise ValueError(
            f"release_date {release_date} 晚于 today {today}，疑似非法数据"
        )
    return (current - released).days


def window_start(state: VendorState, today: str) -> str:
    """新鲜度窗口起点：active 模型最大 release_date（ISO 字典序即时间序）；
    无 active 模型（含 models 为空）回退 today-180 天。
    """
    dates = [model.release_date for model in state.active_models()]
    if dates:
        return max(dates)
    return (date.fromisoformat(today) - timedelta(days=WINDOW_FALLBACK_DAYS)).isoformat()
