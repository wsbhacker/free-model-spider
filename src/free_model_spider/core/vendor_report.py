"""厂商新鲜度报告渲染 v4：五列主表、同名 JSON、README 区块与 -pending 三节明细（纯函数层）。"""

import json
from pathlib import Path
from typing import NamedTuple

from free_model_spider.core.vendor_config import VendorsConfig
from free_model_spider.core.vendor_state import Baseline, days_since

TABLE_HEADER_LINES = [
    "| 厂商 | 模型 | 发布日 | 距今（天） | 证据 |",
    "|---|---|---|---|---|",
]
LEGEND_LINE = "> 发布日与证据均来自厂商官方公告（agent 检索，域名白名单校验）"
FMS_VENDORS_START = "<!-- fms-vendors:start -->"
FMS_VENDORS_END = "<!-- fms-vendors:end -->"
PENDING_EXCERPT_LIMIT = 500  # 拒收条目附 agent 原文前 500 字（spec §7.1）


class ModelRow(NamedTuple):
    """主表一行在表（未退役）模型：厂商显示名、key、展示名、公告 URL、发布日与距今天数。"""

    vendor_name: str
    key: str
    name: str
    url: str | None
    release_date: str
    days: int


class PendingLine(NamedTuple):
    """-pending「未建基线 / 处理异常」或「暂态失败」一节中的一行。"""

    vendor_name: str
    reason: str


class RejectedLine(NamedTuple):
    """-pending「拒收条目」一节中的一行：拒收原因 + agent 原文摘录。"""

    vendor_name: str
    name: str
    reason: str
    excerpt: str


def rows_from_baseline(
    config: VendorsConfig,
    baseline: Baseline,
    today: str,
) -> list[ModelRow]:
    """按配置厂商序产出在表模型行；组内按 release_date 倒序，retired 不出现。

    基线中有而 yml 已移除的厂商不渲染（状态块仍随基线复制保留，spec §5）。
    """
    states = {state.id: state for state in baseline.vendors}
    rows: list[ModelRow] = []
    for vendor in config.vendors:
        state = states.get(vendor.id)
        if state is None:
            continue
        active = sorted(state.active_models(),
                        key=lambda model: model.release_date, reverse=True)
        for model in active:
            rows.append(
                ModelRow(
                    vendor_name=vendor.name,
                    key=model.key,
                    name=model.name,
                    url=model.url or None,
                    release_date=model.release_date,
                    days=days_since(model.release_date, today),
                )
            )
    return rows


def _evidence_cell(row: ModelRow) -> str:
    return f"[公告]({row.url})" if row.url else "—"


def _table_lines(rows: list[ModelRow]) -> list[str]:
    lines = [*TABLE_HEADER_LINES]
    for row in rows:
        lines.append(
            f"| {row.vendor_name} | {row.name} | {row.release_date} | "
            f"{row.days} | {_evidence_cell(row)} |"
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
        f"基线快照：data/vendor-releases/{date}.json · 口径：官方公告日（Asia/Shanghai）",
        "",
    ]
    return "\n".join(lines)


def render_json(date: str, rows: list[ModelRow]) -> str:
    """与 md 同名的 JSON：schema_version/date 头 + models[]（字段序固定，url 可为 null）。"""
    payload = {
        "schema_version": 4,
        "date": date,
        "models": [
            {
                "vendor": row.vendor_name,
                "key": row.key,
                "name": row.name,
                "url": row.url,
                "release_date": row.release_date,
                "days": row.days,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_pending_markdown(
    date: str,
    failed: list[PendingLine],
    transient: list[PendingLine],
    rejected: list[RejectedLine],
) -> str:
    """-pending 后缀明细报告（spec §9）：三节，空节省略；拒收行含原文摘录前 500 字。"""
    lines = [f"# 各大厂商大模型新鲜度 {date} 未决明细"]
    if failed:
        lines += ["", "## 未建基线 / 处理异常", ""]
        lines += [f"- {line.vendor_name}：{line.reason}" for line in failed]
    if transient:
        lines += ["", "## 暂态失败（下次运行重试）", ""]
        lines += [f"- {line.vendor_name}：{line.reason}" for line in transient]
    if rejected:
        lines += ["", "## 拒收条目", ""]
        for line in rejected:
            lines.append(f"- {line.vendor_name} / {line.name}：{line.reason}")
            lines.append(f"  > {line.excerpt[:PENDING_EXCERPT_LIMIT]}")
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
