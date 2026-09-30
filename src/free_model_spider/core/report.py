import re
from pathlib import Path
from typing import NamedTuple

from free_model_spider.core.diff import DiffResult
from free_model_spider.core.intro import IntroCard
from free_model_spider.sources.base import ModelRecord, sort_records

INDEX_START = "<!-- fms-index:start -->"
INDEX_END = "<!-- fms-index:end -->"


class IndexEntry(NamedTuple):
    date: str
    source: str
    display: str
    added: int
    removed: int
    path: str


_SOURCE_PAGE_URLS = {"openrouter": "https://openrouter.ai/{id}"}


def _page_url(record: ModelRecord) -> str:
    return record.page_url or _SOURCE_PAGE_URLS.get(record.source, "").format(id=record.id)


def _fmt_context(record: ModelRecord) -> str:
    return f"{record.context_length:,}" if record.context_length else "未知"


def _fmt_modalities(record: ModelRecord) -> str:
    inputs = "、".join(record.input_modalities) or "未知"
    outputs = "、".join(record.output_modalities) or "未知"
    return f"{inputs}→{outputs}"


def _added_section(records: list[ModelRecord], intros: dict[str, IntroCard]) -> list[str]:
    lines = [f"## 🆕 新增 ({len(records)})" if records else "## 🆕 新增——无变化"]
    if not records:
        lines.append("- 无")
        return lines
    for r in records:
        card = intros.get(r.id) or IntroCard(model_id=r.id, kind="metadata", title=r.name)
        url = _page_url(r)
        lines.append(f"### {r.id}")
        lines.append(f"- 名称：{card.title}")
        lines.append(f"- 介绍：{card.summary}")
        if card.highlights:
            lines.append(f"- 要点：{'；'.join(card.highlights)}")
        if card.caveat:
            lines.append(f"- 注意：{card.caveat}")
        links = [f"[平台页]({url})"] if url else []
        links += [f"<{u}>" for u in card.sources if u != url]
        if links:
            lines.append(f"- 链接：{' ｜ '.join(links)}")
    return lines


def _removed_section(records: list[ModelRecord], yesterday_date: str | None) -> list[str]:
    lines = [f"## 🗑️ 移除 ({len(records)})" if records else "## 🗑️ 移除——无变化"]
    if not records:
        lines.append("- 无")
        return lines
    for r in records:
        url = _page_url(r)
        link = f"[{r.id}]({url})" if url else r.id
        last_seen = f"（最后出现：{yesterday_date}）" if yesterday_date else ""
        lines.append(f"- {link}{last_seen}")
    return lines


def _unchanged_section(records: list[ModelRecord]) -> list[str]:
    lines = [f"## 📋 无变化 ({len(records)})"]
    if not records:
        lines.append("- 无")
        return lines
    lines += ["| 模型 | 名称 | 上下文 | 模态 |", "|---|---|---|---|"]
    for r in records:
        url = _page_url(r)
        link = f"[{r.id}]({url})" if url else r.id
        lines.append(f"| {link} | {r.name} | {_fmt_context(r)} | {_fmt_modalities(r)} |")
    return lines


def render_report(
    display_name: str,
    date: str,
    records: list[ModelRecord],
    diff: DiffResult,
    intros: dict[str, IntroCard],
    yesterday_date: str | None,
    snapshot_rel: str,
    anomalies: list[str] | None = None,
) -> str:
    header = f"# {display_name} 免费模型清单 {date}"
    if yesterday_date:
        stats = (
            f"> 免费 {len(records)} 个（{yesterday_date}"
            f"：+{len(diff.added)} / -{len(diff.removed)}）"
        )
    else:
        # 首日基线：无增删可报，全部模型归入"无变化"清单
        stats = f"> 免费 {len(records)} 个（首日基线）"
        diff = DiffResult(added=[], removed=[], unchanged=sort_records(records))
    lines = [header, stats, "", *_added_section(sort_records(diff.added), intros),
             "", *_removed_section(sort_records(diff.removed), yesterday_date),
             "", *_unchanged_section(sort_records(diff.unchanged))]
    if anomalies:
        lines += ["", f"## ⚠️ 异常 ({len(anomalies)})"]
        lines += [f"- {a}" for a in anomalies]
    lines += ["", "---", f"快照：{snapshot_rel}", ""]
    return "\n".join(lines)


_ROW_RE = re.compile(r"^\| (\d{4}-\d{2}-\d{2}) \| (\S+) \|")


def update_readme_index(readme: Path, entries: list[IndexEntry]) -> None:
    entries = [IndexEntry(*e) for e in entries]  # 兼容裸元组
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
    else:
        text = "# free-model-spider\n"
    start = text.find(INDEX_START)
    end = text.find(INDEX_END)
    rows: list[tuple[str, str, str]] = []  # (date, 显示名, raw_line)
    if start != -1 and end != -1:
        for line in text[start + len(INDEX_START) : end].splitlines():
            m = _ROW_RE.match(line)
            if m:
                rows.append((m.group(1), m.group(2), line))
    else:
        text = text.rstrip("\n") + "\n\n## 日报索引\n\n"
        start, end = -1, -1
    today_keys = {(e.date, e.display) for e in entries}
    rows = [r for r in rows if (r[0], r[1]) not in today_keys]
    for e in entries:
        rows.append((e.date, e.source,
                     f"| {e.date} | {e.display} | +{e.added} | -{e.removed} | "
                     f"[链接]({e.path}) |"))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    block = "\n".join(
        ["| 日期 | 平台 | 新增 | 移除 | 日报 |", "|---|---|---|---|---|",
         *[r[2] for r in rows]]
    )
    if start != -1 and end != -1:
        text = text[: start + len(INDEX_START)] + "\n" + block + "\n" + text[end:]
    else:
        text = text + f"{INDEX_START}\n{block}\n{INDEX_END}\n"
    readme.write_text(text, encoding="utf-8")
