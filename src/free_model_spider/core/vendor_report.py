"""厂商新鲜度报告渲染 v3：模型级 md 长表、同名 JSON、README 区块与 -pending 明细（纯函数层）。"""

import json
from pathlib import Path
from typing import NamedTuple

from free_model_spider.core.vendor_config import VendorReleasesConfig
from free_model_spider.core.vendor_stats import Baseline, days_since

TABLE_HEADER_LINES = ["| 厂商 | 模型 | 发布日 | 天数 |", "|---|---|---|---|"]
LEGEND_LINE = (
    "> 模型名链接 = 官方公告页（official，域名白名单校验）或 ⧉ 目录平台页（directory 来源）；"
    "`†` = 目录日期（models.dev 策展 / OpenRouter 上架），可能滞后官方公告数日"
)
FMS_VENDORS_START = "<!-- fms-vendors:start -->"
FMS_VENDORS_END = "<!-- fms-vendors:end -->"


class ModelRow(NamedTuple):
    """主表一行已确认模型：厂商显示名、模型标识/展示名、链接、佐证、发布日与天数、日期来源。"""

    vendor_name: str
    key: str
    name: str
    url: str | None
    corroborating: str | None
    release_date: str
    days: int
    source: str


class PendingRow(NamedTuple):
    """-pending 明细一行未确认模型：厂商显示名、模型名与未确认原因。"""

    vendor_name: str
    name: str
    reason: str


def rows_from_baseline(
    config: VendorReleasesConfig,
    baseline: Baseline,
    today: str,
    corroborations: dict[str, str],
) -> list[ModelRow]:
    """按配置厂商序、组内配置序产出已确认（confirmed）模型行。

    corroborations 为 model.key -> ⧉ 平台页 URL（cli 侧由目录记录构造，
    OpenRouter 优先）；unconfirmed 模型不出现（进 -pending 明细）。
    """
    states = {state.id: state for state in baseline.vendors}
    rows: list[ModelRow] = []
    for vendor in config.vendors:
        state = states.get(vendor.id)
        if state is None:
            continue
        model_states = {m.key: m for m in state.models}
        for model in vendor.models:
            model_state = model_states.get(model.key)
            if model_state is None or model_state.status != "confirmed":
                continue
            if not model_state.date:
                continue
            rows.append(
                ModelRow(
                    vendor_name=vendor.name,
                    key=model.key,
                    name=model_state.name or model.name,
                    url=model_state.url,
                    corroborating=corroborations.get(model.key),
                    release_date=model_state.date,
                    days=days_since(model_state.date, today),
                    source=model_state.source or "official",
                )
            )
    return rows


def _model_cell(row: ModelRow) -> str:
    link = row.url or row.corroborating
    cell = f"[{row.name}]({link})" if link else row.name
    if row.corroborating and row.url:
        cell += f" [⧉]({row.corroborating})"
    return cell


def _date_cell(row: ModelRow) -> str:
    return f"{row.release_date}†" if row.source == "directory" else row.release_date


def _table_lines(rows: list[ModelRow]) -> list[str]:
    """四列表行；厂商名仅组首行显示，与前一行同厂商则输出空字符串。"""
    lines = [*TABLE_HEADER_LINES]
    previous_vendor: str | None = None
    for row in rows:
        vendor_name = "" if row.vendor_name == previous_vendor else row.vendor_name
        previous_vendor = row.vendor_name
        lines.append(
            f"| {vendor_name} | {_model_cell(row)} | {_date_cell(row)} | {row.days} |"
        )
    return lines


def render_markdown(date: str, rows: list[ModelRow]) -> str:
    """极简日报：标题 + 一张表 + 图例行 + 页脚口径行，别无其他小节。"""
    lines = [
        f"# 各大厂商大模型新鲜度 {date}",
        "",
        "## 🥬 各大厂商大模型新鲜度",
        "",
        *_table_lines(rows),
        "",
        LEGEND_LINE,
        "",
        "---",
        f"基线快照：data/vendor-models/{date}.json · 口径：官方公告日（Asia/Shanghai）",
        "",
    ]
    return "\n".join(lines)


def render_json(date: str, rows: list[ModelRow]) -> str:
    """与 md 同名的 JSON：schema_version/date 头 + models[]（字段序固定，url 可为 null）。"""
    payload = {
        "schema_version": 1,
        "date": date,
        "models": [
            {
                "vendor": row.vendor_name,
                "key": row.key,
                "name": row.name,
                "url": row.url,
                "release_date": row.release_date,
                "source": row.source,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def pending_rows_from_baseline(
    config: VendorReleasesConfig, baseline: Baseline
) -> list[PendingRow]:
    """收集全部 unconfirmed 模型（当日未能确认、未进主表），按配置厂商/模型顺序。"""
    states = {state.id: state for state in baseline.vendors}
    rows: list[PendingRow] = []
    for vendor in config.vendors:
        state = states.get(vendor.id)
        if state is None:
            continue
        model_states = {m.key: m for m in state.models}
        for model in vendor.models:
            model_state = model_states.get(model.key)
            if model_state is None or model_state.status != "unconfirmed":
                continue
            rows.append(
                PendingRow(
                    vendor_name=vendor.name,
                    name=model_state.name or model.name,
                    reason=model_state.reason,
                )
            )
    return rows


def render_pending_markdown(
    date: str, rows: list[PendingRow], failed_vendors: list[str] | None = None
) -> str:
    """-pending 后缀明细报告：unconfirmed 模型清单 + 处理失败厂商脚注（有错才生成）。"""
    lines = [
        f"# 各大厂商大模型新鲜度 {date} 未决明细",
        "",
        "| 厂商 | 模型 | 原因 |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.vendor_name} | {row.name} | {row.reason or '—'} |"
        )
    lines += [
        "",
        "> 提取失败与公告源不可用均为暂态，次日自动重试；"
        "「公告与目录均无可用日期」的模型需检查 match 正则或补充公告源。",
    ]
    if failed_vendors:
        lines += ["", f"> 处理失败厂商（当日整体跳过）：{'、'.join(failed_vendors)}"]
    lines.append("")
    return "\n".join(lines)


def update_readme_freshness(readme: Path, date: str, rows: list[ModelRow]) -> None:
    """重写 README 两标记间的厂商新鲜度区块；标记不存在则追加到文末（含标记）。

    模式对齐 report.py 的 update_readme_index：find/replace 标记、文末追加。
    """
    rows = [ModelRow(*r) for r in rows]  # 兼容裸元组
    block = "\n".join(
        ["### 厂商新鲜度榜", "", *_table_lines(rows), "", f"> 更新于 {date}"]
    )
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    start = text.find(FMS_VENDORS_START)
    end = text.find(FMS_VENDORS_END)
    if start != -1 and end != -1:
        text = text[: start + len(FMS_VENDORS_START)] + "\n" + block + "\n" + text[end:]
    else:
        text = (text.rstrip("\n") + "\n\n" if text else "") + (
            f"{FMS_VENDORS_START}\n{block}\n{FMS_VENDORS_END}\n"
        )
    readme.write_text(text, encoding="utf-8")
