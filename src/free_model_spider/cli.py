import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from free_model_spider.core.alert import run_link, send_alert
from free_model_spider.core.diff import DiffResult, diff_models, dump_diff_json
from free_model_spider.core.intro import build_intro
from free_model_spider.core.report import IndexEntry, render_report, update_readme_index
from free_model_spider.core.snapshot import (
    find_latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from free_model_spider.core.vendor_config import load_config
from free_model_spider.core.vendor_judge import (
    classify_entry,
    confirm_from_page,
    extract_line,
)
from free_model_spider.core.vendor_report import (
    render_json,
    render_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_stats import (
    LineState,
    VendorState,
    apply_judgment,
    baseline_path,
    collect_directory_entries,
    detect_review,
    empty_baseline,
    finalize_state,
    find_latest_baseline,
    load_baseline,
    save_baseline,
    screen_new_entries,
    update_seen,
)
from free_model_spider.llm.client import build_llm_client
from free_model_spider.search import build_search_provider
from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import sort_records

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _git_commit(date: str, data_dir: Path, reports_dir: Path, readme: Path,
                message: str | None = None) -> None:
    if message is None:
        message = f"docs: 免费模型日报 {date} [skip ci]"
    subprocess.run(["git", "add", str(data_dir), str(reports_dir), str(readme)],
                   check=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        # git 把"无内容可提交"写到 stdout：含 "nothing to commit..." 与
        # "nothing added to commit..." 两个变体，统一用 b"nothing" 匹配
        if b"nothing" in result.stdout:
            print("git commit: nothing to commit, skip")
            return
        result.check_returncode()  # 真实 git 故障 → 非零退出，Actions 红灯（spec §11）
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "push"], check=True)


def _freeze_content(vendor_name: str, line_name: str, pending_days: int) -> str:
    """冻结告警正文：厂商/线/pending 天数 + 运行链接（非 Actions 环境为空串）。"""
    link = run_link()
    tail = f"\n{link}" if link else ""
    return (f"{vendor_name}「{line_name}」pending 连续 {pending_days} 天未决，"
            f"已冻结为 stale-baseline{tail}")


def run_vendors(args) -> int:
    """fms vendors 编排：目录→检测→LLM 裁决→基线→报告→提交（spec §4）。

    厂商循环内 try/except 单厂商隔离（模式对齐主循环 cli.py 的单源隔离）；
    整个编排由顶层 try/except 兜底：任何未捕获异常/写盘失败先告警再非零退出。
    """
    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60, follow_redirects=True)
    failed: list[str] = []
    try:
        config = load_config(args.config)
        latest = find_latest_baseline(args.data_dir, date)
        baseline = load_baseline(latest) if latest else empty_baseline(date, config)
        baseline.date = date
        states = {v.id: v for v in baseline.vendors}
        for vendor in config.vendors:
            if vendor.id not in states:  # 配置新增厂商：补全 pending 线随当日基线落盘
                state = VendorState(id=vendor.id)
                state.lines = [LineState(key=line.key) for line in vendor.lines]
                states[vendor.id] = state
                baseline.vendors.append(state)
        entries_by_vendor, dir_errors = collect_directory_entries(client, config)
        for error in dir_errors:
            print(f"vendors: 目录采集降级：{error}")
        review = detect_review(config, baseline, entries_by_vendor, date)
        llm = build_llm_client(client)
        search = build_search_provider(client)
        corroborations: dict[str, str] = {}

        for vendor in config.vendors:
            state = states[vendor.id]
            try:
                for line in vendor.lines:
                    if line.override is None:
                        continue
                    # override 线载入即直接采用，跳过事件检测与 LLM 裁决（spec §5）
                    for i, line_state in enumerate(state.lines):
                        if line_state.key == line.key:
                            state.lines[i] = LineState(
                                key=line.key, model=line.override.model,
                                release_date=line.override.release_date,
                                url=line.override.source, status="ok",
                                reason="override 直接采用", last_verified=date,
                            )
                if review.get(vendor.id) is None:
                    # 未触发厂商：零 LLM，逐线收尾（ok 重臂 / pending 满期冻结）
                    line_names = {line.key: line.name for line in vendor.lines}
                    for i, line_state in enumerate(state.lines):
                        new_line = finalize_state(line_state, date)
                        state.lines[i] = new_line
                        if (new_line.status == "stale-baseline"
                                and line_state.status != "stale-baseline"):
                            print(f"vendors: {vendor.id}/{new_line.key} 冻结为 stale-baseline")
                            send_alert(client, "大模型新鲜度厂商冻结", _freeze_content(
                                vendor.name, line_names.get(new_line.key, new_line.key),
                                new_line.pending_days))
                    continue
                print(f"vendors: {vendor.id} 触发复核：{'、'.join(review[vendor.id])}")
                entries = entries_by_vendor.get(vendor.id, [])
                candidates = screen_new_entries(vendor, state, entries)
                labels: dict[str, tuple[str, str]] = {}
                entry_line: dict[str, str] = {}
                ignore_notes: list[str] = []
                for entry in candidates:  # 新条目先归类（决定该线是否由条目触发）
                    category, why = classify_entry(llm, vendor, entry)
                    labels[entry] = (category, why)
                    if category in ("flagship", "sub"):
                        entry_line[entry] = category
                    elif category == "ignore":
                        ignore_notes.append(f"{entry}（{why}）")
                line_states = {line_state.key: line_state for line_state in state.lines}
                for line in vendor.lines:
                    if line.override is not None:
                        continue  # 已在载入期直接采用
                    old = line_states[line.key]
                    judgment, _ = extract_line(llm, search, vendor, line, search_sleep=1.1)
                    if judgment is not None and (
                        judgment.release_date != old.release_date
                        or judgment.model != old.model
                    ):
                        # 与基线不一致（新版本/新日期/首日）：抓原文二次确认（spec §7.4）；
                        # 确认失败置 None → apply_judgment 降级 pending 保留旧基线
                        judgment = confirm_from_page(llm, client, vendor, judgment)
                    new_line = finalize_state(apply_judgment(old, judgment, date), date)
                    line_states[line.key] = new_line
                    if judgment is not None and new_line.status == "ok":
                        for entry, key in entry_line.items():
                            if key == line.key and "/" in entry:
                                # OpenRouter 条目可拼平台页佐证；models.dev（':' 前缀）跳过
                                corroborations[new_line.model] = (
                                    f"https://openrouter.ai/{entry}"
                                )
                    if (new_line.status == "stale-baseline"
                            and old.status != "stale-baseline"):
                        print(f"vendors: {vendor.id}/{line.key} 冻结为 stale-baseline")
                        send_alert(client, "大模型新鲜度厂商冻结", _freeze_content(
                            vendor.name, line.name, new_line.pending_days))
                for i, line_state in enumerate(state.lines):
                    if line_state.key in line_states:
                        state.lines[i] = line_states[line_state.key]
                if ignore_notes:  # ignore 判定只进基线审计字段，报告不呈现（spec §7.1）
                    audit_key = next(
                        (line.key for line in vendor.lines if line.override is None), None
                    )
                    for i, line_state in enumerate(state.lines):
                        if audit_key is not None and line_state.key == audit_key:
                            note = f"ignore 审计：{'；'.join(ignore_notes)}"
                            state.lines[i] = line_state.model_copy(update={
                                "reason": (f"{line_state.reason}；{note}"
                                           if line_state.reason else note),
                            })
                            break
                # entries 传目录全量（非预筛候选），labels 键为 casefolded 条目 id
                state.seen_directory_ids = update_seen(vendor, state, entries, labels)
            except Exception as exc:  # 单厂商隔离：一家失败只损失该家，其余照常出报告
                failed.append(vendor.id)
                print(f"vendors: {vendor.id} 处理失败，跳过该厂商：{exc!r}")
                for i, line_state in enumerate(state.lines):
                    state.lines[i] = line_state.model_copy(
                        update={"reason": f"厂商处理失败：{exc!r}"})
                continue

        rows = rows_from_baseline(config, baseline, date, corroborations)
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        report = args.reports_dir / f"各大厂商大模型新鲜度-{date}.md"
        report.write_text(render_markdown(date, rows), encoding="utf-8")
        report.with_suffix(".json").write_text(render_json(date, rows), encoding="utf-8")
        update_readme_freshness(args.readme, date, rows)
        save_baseline(baseline, baseline_path(args.data_dir, date))
        print(f"vendors: 报告 -> {report}")
        if not args.no_commit:
            _git_commit(date, args.data_dir, args.reports_dir, args.readme,
                        message=f"docs: 大模型新鲜度日报 {date} [skip ci]")
    except Exception as exc:  # 顶层兜底：日报失败必须先告警再非零退出（spec §10.1）
        print(f"vendors: 处理失败：{exc!r}")
        send_alert(client, "大模型新鲜度日报失败",
                   f"{date} 大模型新鲜度日报失败：{exc!r}\n{run_link()}")
        return 1
    if failed:
        # 单厂商失败不阻断整体产物；非零退出让 Actions 红灯可见（模式对齐 fms run）
        print(f"失败厂商：{'、'.join(failed)}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fms")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--date", default=None, help="YYYY-MM-DD，默认北京时间今天")
    run.add_argument("--sources", default=None, help="逗号分隔，默认全部注册的数据源")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--reports-dir", type=Path, default=Path("reports"))
    run.add_argument("--readme", type=Path, default=Path("README.md"))
    run.add_argument("--no-commit", action="store_true")
    vendors = sub.add_parser("vendors")
    vendors.add_argument("--date", default=None, help="YYYY-MM-DD，默认北京时间今天")
    vendors.add_argument("--data-dir", type=Path, default=Path("data"))
    vendors.add_argument("--reports-dir", type=Path, default=Path("reports"))
    vendors.add_argument("--readme", type=Path, default=Path("README.md"))
    vendors.add_argument("--config", type=Path, default=Path("data/vendor_releases.yml"))
    vendors.add_argument("--no-commit", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "vendors":
        return run_vendors(args)

    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    # follow_redirects：上游文档站偶发整页搬迁（302），跟随以免硬崩
    client = httpx.Client(timeout=60, follow_redirects=True)
    names = args.sources.split(",") if args.sources else available_sources()
    search = build_search_provider(client)
    llm = build_llm_client(client)
    entries: list[IndexEntry] = []
    failed: list[str] = []

    for name in names:
        source = create_source(name, client)
        try:
            records = source.fetch_free_models()
            latest = find_latest_snapshot(args.data_dir, name, date)
            intros: dict = {}
            if latest is None:
                # 首日基线：全部计入存量，不生成介绍（spec §6；render 对 yesterday_date=None 的重映射与此幂等）
                diff = DiffResult(added=[], removed=[], unchanged=sort_records(records))
            else:
                diff = diff_models(records, load_snapshot(latest))
                for record in diff.added:
                    intros[record.id] = build_intro(record, search, llm)
                    if llm is not None:
                        time.sleep(3)
            spath = snapshot_path(args.data_dir, name, date)
            save_snapshot(records, spath, name, date)
            report_rel = args.reports_dir / f"{name}-{date}-免费模型清单.md"
            text = render_report(
                source.display_name, date, records, diff, intros,
                latest.stem if latest else None, spath.as_posix(),
                anomalies=source.last_errors or None,
            )
            args.reports_dir.mkdir(parents=True, exist_ok=True)
            report_rel.write_text(text, encoding="utf-8")
            json_rel = report_rel.with_suffix(".json")
            json_rel.write_text(
                dump_diff_json(
                    diff, source=name, display_name=source.display_name,
                    date=date, baseline_date=latest.stem if latest else None,
                    total=len(records),
                ),
                encoding="utf-8",
            )
            entries.append(IndexEntry(
                date=date, source=name, display=source.display_name,
                added=len(diff.added), removed=len(diff.removed),
                path=report_rel.as_posix(),
            ))
            print(f"{name}: 免费 {len(records)}（+{len(diff.added)} / -{len(diff.removed)}）"
                  f" -> {report_rel}")
        except Exception as exc:  # 单源隔离：上游改版/故障只损失该源，其余源照常出报告并提交
            failed.append(name)
            print(f"{name}: 处理失败，跳过该源：{exc!r}")
            continue

    if entries:
        update_readme_index(args.readme, entries)
    if not args.no_commit:
        _git_commit(date, args.data_dir, args.reports_dir, args.readme)
    if failed:
        send_alert(client, "免费模型日报存在失败源",
                   f"失败源：{'、'.join(failed)}\n{run_link()}")
        # 非零退出让 Actions 红灯可见；成功源产物已在上面照常提交
        print(f"失败源：{'、'.join(failed)}")
        return 1
    return 0
