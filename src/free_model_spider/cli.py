import argparse
import os
import re
import subprocess
import time
from collections import Counter
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
from free_model_spider.core.vendor_config import LineConfig, VendorConfig, load_config
from free_model_spider.core.vendor_judge import (
    classify_entry,
    extract_from_article,
)
from free_model_spider.core.vendor_report import (
    render_json,
    render_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_source import (
    AnnouncementEntry,
    fetch_listings,
)
from free_model_spider.core.vendor_stats import (
    LineState,
    VendorState,
    apply_judgment,
    baseline_path,
    collect_directory_entries,
    empty_baseline,
    finalize_state,
    find_latest_baseline,
    line_matches,
    load_baseline,
    save_baseline,
    screen_new_entries,
)
from free_model_spider.llm.client import LLMClient, build_llm_client
from free_model_spider.search import build_search_provider
from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import sort_records

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 官网抓取 UA：openai 等站点对非浏览器 UA 直接 403（2026-10-30 实测）
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


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


def _slug(text: str) -> str:
    """模型名/目录 id 的归一化形态（仅小写字母数字），用于 ⧉ 佐证模糊匹配。"""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _attach_corroboration(
    vendor: VendorConfig,
    line: LineConfig,
    model: str,
    directory_entries: list[str],
    corroborations: dict[str, str],
) -> None:
    """线当日确认成功后，取门筛命中的首个 OpenRouter 条目拼平台页佐证（⧉，非关键）。"""
    model_slug = _slug(model)
    if not model_slug:
        return
    entry = next(
        (
            e
            for e in directory_entries
            if "/" in e and model_slug in _slug(e) and line_matches(line, e.casefold())
        ),
        None,
    )
    if entry:
        corroborations[model] = f"https://openrouter.ai/{entry}"


def _announcement_key(url: str, title: str) -> str:
    """公告条目去重键：headings/json 模式条目共享列表页 URL，必须带标题区分。"""
    return f"{url}::{title.casefold()}"


def _bootstrap_vendor(
    vendor: VendorConfig,
    state: VendorState,
    listings: list[AnnouncementEntry],
    llm: LLMClient | None,
    client: httpx.Client,
    date: str,
    directory_entries: list[str],
    corroborations: dict[str, str],
) -> None:
    """首跑/基线迁移初始化（v2 spec §4）：每线取门筛命中的日期最新公告做种子
    （每线至多 1 次提取），其余条目全部入 seen——杜绝 v1 首跑 329 次调用重演。

    LLM 未配置时不入 seen（配置好后次日整体重试）；公告列表为空同样不入 seen，
    待列表可解析后自动重跑 bootstrap。
    """
    if llm is None or not listings:
        return
    line_states = {line_state.key: line_state for line_state in state.lines}
    failed_keys: set[str] = set()
    for line in vendor.lines:
        if line.override is not None:
            continue
        candidates = [
            entry
            for entry in listings
            if entry.date and line_matches(line, entry.title.casefold())
        ]
        if not candidates:
            continue
        newest = max(candidates, key=lambda entry: entry.date)  # 平局取文档序在前（更新）
        judgment = extract_from_article(llm, client, vendor, line, newest.url)
        # finalize 统一在本厂商收尾循环做：冻结迁移告警恰一次，裁决阶段只合入裁决
        new_line = apply_judgment(line_states[line.key], judgment, date)
        line_states[line.key] = new_line
        print(f"vendors: {vendor.id}/{line.key} bootstrap <- {newest.title[:60]} -> "
              f"{new_line.status}：{new_line.reason}")
        if judgment is None:
            failed_keys.add(_announcement_key(newest.url, newest.title))
        elif new_line.status == "ok":
            _attach_corroboration(vendor, line, new_line.model, directory_entries,
                                  corroborations)
    for i, line_state in enumerate(state.lines):
        if line_state.key in line_states:
            state.lines[i] = line_states[line_state.key]
    state.seen_announcement_urls = [
        _announcement_key(entry.url, entry.title)
        for entry in listings
        if _announcement_key(entry.url, entry.title) not in failed_keys
    ]


def _process_new_announcements(
    vendor: VendorConfig,
    state: VendorState,
    listings: list[AnnouncementEntry],
    llm: LLMClient | None,
    client: httpx.Client,
    date: str,
    directory_entries: list[str],
    corroborations: dict[str, str],
) -> None:
    """日常增量：只处理未入 seen 的新公告条目——门筛定位线，文章页提取裁决。

    终态（ignore / 线裁决非 pending）入 seen；提取或归类失败次日重试（条目仍在
    列表页的前提下）。列表页条目按文档序处理（通常新→旧，防倒退拒绝兜底乱序）。
    """
    seen = set(state.seen_announcement_urls)
    new_entries = [
        entry
        for entry in listings
        if _announcement_key(entry.url, entry.title) not in seen
    ]
    if not new_entries:
        return
    line_states = {line_state.key: line_state for line_state in state.lines}
    labels: dict[str, tuple[str, str]] = {}
    for entry in new_entries:
        gated = [
            line
            for line in vendor.lines
            if line.override is None and line_matches(line, entry.title.casefold())
        ]
        if gated:  # 命中唯一线直接采用；多线命中取配置序在前（flagship 优先），不烧 LLM
            labels[entry.url + entry.title] = (gated[0].key,
                                               "门筛命中" if len(gated) == 1 else "多线命中取首")
        else:
            category, why = classify_entry(llm, vendor, entry.title)
            print(f"vendors: {vendor.id} 归类 {entry.title[:60]} -> {category}：{why[:80]}")
            labels[entry.url + entry.title] = (category, why)

    for entry in new_entries:
        category, _ = labels[entry.url + entry.title]
        if category == "ignore":
            state.seen_announcement_urls.append(
                _announcement_key(entry.url, entry.title))
            continue
        if category not in ("flagship", "sub"):
            continue  # 归类失败（pending）：不入 seen，次日重试
        line = next(line for line in vendor.lines if line.key == category)
        judgment = extract_from_article(llm, client, vendor, line, entry.url)
        new_line = apply_judgment(line_states[line.key], judgment, date)
        print(f"vendors: {vendor.id}/{line.key} <- {entry.title[:60]} -> "
              f"{new_line.status}：{new_line.reason}")
        line_states[line.key] = new_line
        if judgment is not None and new_line.status == "ok":
            _attach_corroboration(vendor, line, new_line.model, directory_entries,
                                  corroborations)
        if new_line.status != "pending":
            state.seen_announcement_urls.append(
                _announcement_key(entry.url, entry.title))
    for i, line_state in enumerate(state.lines):
        if line_state.key in line_states:
            state.lines[i] = line_states[line_state.key]


def run_vendors(args) -> int:
    """fms vendors 编排 v2：官网公告爬取主通道 + 目录双源佐证（redesign spec §4）。

    每厂商：override 直接采用 → 公告页增量/bootstrap 裁决 → 目录新条目审计 →
    逐线收尾（ok 重臂 / pending 满期冻结）。厂商循环内 try/except 单厂商隔离；
    整个编排由顶层 try/except 兜底：未捕获异常先告警再非零退出。
    """
    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60, follow_redirects=True,
                          headers={"User-Agent": BROWSER_UA})
    failed: list[str] = []
    try:
        config = load_config(args.config)
        latest = find_latest_baseline(args.data_dir, date)
        baseline = load_baseline(latest) if latest else empty_baseline(date, config)
        baseline.date = date
        states = {v.id: v for v in baseline.vendors}
        for vendor in config.vendors:
            state = states.get(vendor.id)
            if state is None:  # 配置新增厂商：补全 pending 线随当日基线落盘
                state = VendorState(id=vendor.id)
                state.lines = [LineState(key=line.key) for line in vendor.lines]
                states[vendor.id] = state
                baseline.vendors.append(state)
                continue
            existing = {line_state.key for line_state in state.lines}
            for line in vendor.lines:  # 既有厂商配置新增产品线：同样补 pending 线
                if line.key not in existing:
                    state.lines.append(LineState(key=line.key))
        entries_by_vendor, dir_errors = collect_directory_entries(client, config)
        for error in dir_errors:
            print(f"vendors: 目录采集降级：{error}")
        llm = build_llm_client(client)
        if llm is None:
            print("vendors: LLM_API_KEY 未配置，公告裁决将降级 pending")
        corroborations: dict[str, str] = {}

        for vendor in config.vendors:
            state = states[vendor.id]
            directory_entries = entries_by_vendor.get(vendor.id, [])
            try:
                for line in vendor.lines:
                    if line.override is None:
                        continue
                    # override 线载入即直接采用，跳过公告裁决（v1 spec §5）
                    for i, line_state in enumerate(state.lines):
                        if line_state.key == line.key:
                            state.lines[i] = LineState(
                                key=line.key, model=line.override.model,
                                release_date=line.override.release_date,
                                url=line.override.source, status="ok",
                                reason="override 直接采用", last_verified=date,
                            )
                if not vendor.announcements:
                    print(f"vendors: {vendor.id} 无公告源配置：仅目录佐证与收尾"
                          f"（需人工 override 兜底）")
                else:
                    listings, list_errors = fetch_listings(client, vendor)
                    for error in list_errors:
                        print(f"vendors: {vendor.id} 公告列表抓取降级：{error}")
                    if not listings and vendor.announcements:
                        print(f"vendors: {vendor.id} 公告列表解析零条目："
                              f"疑似上游改版，沿用昨日基线")
                    if not state.seen_announcement_urls:
                        _bootstrap_vendor(vendor, state, listings, llm, client, date,
                                          directory_entries, corroborations)
                    else:
                        _process_new_announcements(vendor, state, listings, llm, client,
                                                   date, directory_entries, corroborations)
                # 目录新条目审计（v2：门铃降级为"公告页没动静"的提示信号，spec §2）
                fresh_dir = screen_new_entries(vendor, state, directory_entries)
                if fresh_dir and not vendor.announcements:
                    print(f"vendors: {vendor.id} 目录新条目 {len(fresh_dir)} 个但无公告源，"
                          f"人工关注：{fresh_dir[:3]}")
                    state.seen_directory_ids.extend(fresh_dir)
                elif fresh_dir:
                    print(f"vendors: {vendor.id} 目录新条目 {len(fresh_dir)} 个")
                    state.seen_directory_ids.extend(fresh_dir)
                # 逐线收尾：ok 重臂 / pending 满期冻结（状态机出口唯一，冻结告警恰一次）
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
            except Exception as exc:  # 单厂商隔离：一家失败只损失该家，其余照常出报告
                failed.append(vendor.id)
                print(f"vendors: {vendor.id} 处理失败，跳过该厂商：{exc!r}")
                for i, line_state in enumerate(state.lines):
                    state.lines[i] = line_state.model_copy(
                        update={"reason": f"厂商处理失败：{exc!r}"})
                continue

        rows = rows_from_baseline(config, baseline, date, corroborations)
        status_counts = Counter(
            line.status for v in baseline.vendors for line in v.lines
        )
        print(f"vendors: 线状态汇总：ok={status_counts.get('ok', 0)}"
              f" pending={status_counts.get('pending', 0)}"
              f" stale-baseline={status_counts.get('stale-baseline', 0)}"
              f"，进表 {len(rows)} 行")
        if not rows:
            print("vendors: 报告为空：没有任何线同时具备 model/url/release_date，"
                  "检查上方各线降级原因")
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
    except Exception as exc:  # 顶层兜底：日报失败必须先告警再非零退出（v1 spec §10.1）
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
    fail_reasons: dict[str, str] = {}

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
            fail_reasons[name] = repr(exc)[:200]  # 告警摘要截断 200 字（spec §10.1）
            print(f"{name}: 处理失败，跳过该源：{exc!r}")
            continue

    if entries:
        update_readme_index(args.readme, entries)
    if not args.no_commit:
        try:
            _git_commit(date, args.data_dir, args.reports_dir, args.readme)
        except Exception as exc:  # 提交失败：先告警再照旧抛出（保持红灯，spec §10.1）
            send_alert(client, "免费模型日报提交失败",
                       f"日期：{date}\n免费模型日报 git 提交失败："
                       f"{type(exc).__name__}: {exc}\n{run_link()}")
            raise
    if failed:
        detail = "\n".join(f"{name}：{fail_reasons[name]}" for name in failed)
        send_alert(client, "免费模型日报失败源",
                   f"日期：{date}\n失败源：{'、'.join(failed)}\n"
                   f"摘要：\n{detail}\n{run_link()}")
        # 非零退出让 Actions 红灯可见；成功源产物已在上面照常提交
        print(f"失败源：{'、'.join(failed)}")
        return 1
    return 0
