"""厂商大模型新鲜度报告渲染：md 长表、同名 JSON 与 README 常驻区块（纯函数层）。"""

import json
from pathlib import Path
from typing import NamedTuple

from free_model_spider.core.vendor_config import VendorReleasesConfig
from free_model_spider.core.vendor_stats import Baseline, days_since

TABLE_HEADER_LINES = ["| 厂商 | 模型 | 发布日 | 天数 |", "|---|---|---|---|"]
LEGEND_LINE = (
    "> 模型名链接 = 官方公告/产品页（进表硬条件，域名经白名单校验）；"
    "`⧉` = 第三方目录佐证（OpenRouter / models.dev 平台页）"
)
FMS_VENDORS_START = "<!-- fms-vendors:start -->"
FMS_VENDORS_END = "<!-- fms-vendors:end -->"


class VendorRow(NamedTuple):
    """报告中一行模型：厂商显示名、模型名、官方 URL、第三方佐证 URL、发布日与天数。"""

    vendor_name: str
    model: str
    url: str
    corroborating: str | None
    release_date: str
    days: int


def rows_from_baseline(
    config: VendorReleasesConfig,
    baseline: Baseline,
    today: str,
    corroborations: dict[str, str],
) -> list[VendorRow]:
    """按配置厂商顺序、组内线顺序（flagship 在前）产出报告行。

    只收 model/url/release_date 均非空的线——pending、冻结且从未确认的线不出现；
    corroborations 为 model -> 第三方平台页 URL（cli 侧由 OpenRouter 条目构造
    ``https://openrouter.ai/{id}``），无命中则该行无佐证。
    """
    states = {state.id: state for state in baseline.vendors}
    rows: list[VendorRow] = []
    for vendor in config.vendors:
        state = states.get(vendor.id)
        if state is None:
            continue
        line_states = {line.key: line for line in state.lines}
        for line in vendor.lines:
            line_state = line_states.get(line.key)
            if line_state is None:
                continue
            if not (line_state.model and line_state.url and line_state.release_date):
                continue
            rows.append(
                VendorRow(
                    vendor_name=vendor.name,
                    model=line_state.model,
                    url=line_state.url,
                    corroborating=corroborations.get(line_state.model),
                    release_date=line_state.release_date,
                    days=days_since(line_state.release_date, today),
                )
            )
    return rows


def _model_cell(row: VendorRow) -> str:
    cell = f"[{row.model}]({row.url})"
    if row.corroborating:
        cell += f" [⧉]({row.corroborating})"
    return cell


def _table_lines(rows: list[VendorRow]) -> list[str]:
    """四列表行；厂商名仅组首行显示，与前一行同厂商则输出空字符串。"""
    lines = [*TABLE_HEADER_LINES]
    previous_vendor: str | None = None
    for row in rows:
        vendor_name = "" if row.vendor_name == previous_vendor else row.vendor_name
        previous_vendor = row.vendor_name
        lines.append(
            f"| {vendor_name} | {_model_cell(row)} | {row.release_date} | {row.days} |"
        )
    return lines


def render_markdown(date: str, rows: list[VendorRow]) -> str:
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
        f"基线快照：data/vendor-releases/{date}.json · 口径：官方公告日（Asia/Shanghai）",
        "",
    ]
    return "\n".join(lines)


def render_json(date: str, rows: list[VendorRow]) -> str:
    """与 md 同名的 JSON：schema_version/date 头 + models[]（字段序固定）。"""
    payload = {
        "schema_version": 1,
        "date": date,
        "models": [
            {
                "vendor": row.vendor_name,
                "name": row.model,
                "url": row.url,
                "release_date": row.release_date,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def update_readme_freshness(readme: Path, date: str, rows: list[VendorRow]) -> None:
    """重写 README 两标记间的厂商新鲜度区块；标记不存在则追加到文末（含标记）。

    模式对齐 report.py 的 update_readme_index：find/replace 标记、文末追加。
    """
    rows = [VendorRow(*r) for r in rows]  # 兼容裸元组
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
