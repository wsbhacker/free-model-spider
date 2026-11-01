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
from free_model_spider.core.vendor_config import VendorConfig, load_config
from free_model_spider.core.vendor_confirm import resolve_model
from free_model_spider.core.vendor_report import (
    pending_rows_from_baseline,
    render_json,
    render_markdown,
    render_pending_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_source import fetch_listings
from free_model_spider.core.vendor_stats import (
    DirectoryRecord,
    ModelState,
    VendorState,
    baseline_path,
    collect_directory_entries,
    discover_unmaintained,
    empty_baseline,
    find_latest_baseline,
    is_variant_record,
    load_baseline,
    model_matches,
    save_baseline,
)
from free_model_spider.llm.client import build_llm_client
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


def _corroboration_map(
    vendor: VendorConfig, records: list[DirectoryRecord]
) -> dict[str, str]:
    """model.key -> ⧉ 平台页 URL：OpenRouter 家条目优先，无则 models.dev（非关键佐证）。"""
    result: dict[str, str] = {}
    for model in vendor.models:
        matched = [r for r in records if model_matches(model, r.id.casefold())]
        openrouter = next(
            (r for r in matched
             if r.origin == "openrouter" and not is_variant_record(r)), None)
        if openrouter is not None:
            result[model.key] = f"https://openrouter.ai/{openrouter.id}"
            continue
        modelsdev = next((r for r in matched if r.origin == "modelsdev"), None)
        if modelsdev is not None:
            provider, _, model_id = modelsdev.id.partition(":")
            result[model.key] = f"https://models.dev/{provider}/{model_id}"
    return result


def run_vendors(args) -> int:
    """fms vendors 编排 v3：人工圈定模型清单 + 全自动日期提取（spec 2026-11-01 §5）。

    每厂商：公告列表抓取（失败=暂态）→ 逐未确认模型按通道优先级解析（confirmed
    跳过、manual 恒生效）→ 目录未维护新模型发现告警 → 全量条目入 seen。
    厂商循环内 try/except 单厂商隔离；顶层 try/except 兜底先告警再非零退出。
    """
    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60, follow_redirects=True,
                          headers={"User-Agent": BROWSER_UA})
    failed: list[str] = []
    run_failures: list[str] = []  # 当次运行提取失败/公告源不可用明细（收尾统一告警）
    try:
        config = load_config(args.config)
        latest = find_latest_baseline(args.data_dir, date)
        first_run = latest is None
        baseline = load_baseline(latest) if latest else empty_baseline(date, config)
        baseline.date = date
        states = {v.id: v for v in baseline.vendors}
        for vendor in config.vendors:
            state = states.get(vendor.id)
            if state is None:  # 配置新增厂商：补全 unconfirmed 模型随当日基线落盘
                state = VendorState(id=vendor.id)
                state.models = [ModelState(key=m.key, name=m.name)
                                for m in vendor.models]
                states[vendor.id] = state
                baseline.vendors.append(state)
                continue
            model_states = {m.key: m for m in state.models}
            for model in vendor.models:  # 新增模型补条目；展示名以 yml 为准即时生效
                if model.key in model_states:
                    model_states[model.key].name = model.name
                else:
                    state.models.append(ModelState(key=model.key, name=model.name))
        records_by_vendor, dir_errors = collect_directory_entries(client, config)
        for error in dir_errors:
            print(f"vendors: 目录采集降级：{error}")
        llm = build_llm_client(client)
        if llm is None:
            print("vendors: LLM_API_KEY 未配置，有公告源厂商的模型将保持未确认（次日重试）")
        corroborations: dict[str, str] = {}

        for vendor in config.vendors:
            state = states[vendor.id]
            records = records_by_vendor.get(vendor.id, [])
            try:
                listings = None
                if vendor.announcements:
                    merged, list_errors = fetch_listings(client, vendor)
                    for error in list_errors:
                        print(f"vendors: {vendor.id} 公告列表抓取降级：{error}")
                    listings = merged if merged else None  # 空=当日不可用（暂态）
                    if listings is None:
                        print(f"vendors: {vendor.id} 公告列表当日不可用，"
                              f"未确认模型保持未确认次日重试")
                else:
                    print(f"vendors: {vendor.id} 无公告源：未确认模型走目录日期通道")
                model_states = {m.key: m for m in state.models}
                for model in vendor.models:
                    if model.manual is not None:  # manual 纠偏恒生效（覆盖既有 confirmed）
                        model_states[model.key] = resolve_model(
                            vendor, model, listings, records, llm, client, date)
                        continue
                    current = model_states.get(model.key)
                    if current is not None and current.status == "confirmed":
                        current.last_verified = date  # 事实永固，仅记核实日
                        continue
                    new_state = resolve_model(
                        vendor, model, listings, records, llm, client, date)
                    print(f"vendors: {vendor.id}/{model.key} -> "
                          f"{new_state.status}：{new_state.reason}")
                    model_states[model.key] = new_state
                    if (new_state.status == "unconfirmed"
                            and "暂态" in new_state.reason):
                        run_failures.append(
                            f"{vendor.id}/{model.key} {new_state.reason}")
                state.models = list(model_states.values())
                corroborations.update(_corroboration_map(vendor, records))

                unmaintained = discover_unmaintained(vendor, state, records)
                if unmaintained and first_run:
                    print(f"vendors: {vendor.id} 首跑播种：目录条目全部入 seen，"
                          f"未维护条目 {len(unmaintained)} 个不告警")
                elif unmaintained:
                    print(f"vendors: {vendor.id} 发现未维护新模型 {len(unmaintained)} 个："
                          f"{unmaintained[:3]}")
                    link = run_link()
                    tail = f"\n{link}" if link else ""
                    send_alert(client, "发现未维护新模型",
                               f"{date} {vendor.name} 目录出现不匹配任何已维护模型的新条目：\n"
                               + "\n".join(f"- {item}" for item in unmaintained[:3])
                               + "\n决定追踪就加入 data/vendor_releases.yml 的 models" + tail)
                seen = {entry.casefold() for entry in state.seen_directory_ids}
                for record in records:  # 全部当日条目入 seen（含命中模型的）
                    folded = record.id.casefold()
                    if folded not in seen:
                        state.seen_directory_ids.append(folded)
                        seen.add(folded)
            except Exception as exc:  # 单厂商隔离：一家失败只损失该家，其余照常出报告
                failed.append(vendor.id)
                print(f"vendors: {vendor.id} 处理失败，跳过该厂商：{exc!r}")
                continue

        rows = rows_from_baseline(config, baseline, date, corroborations)
        confirmed = sum(1 for v in baseline.vendors for m in v.models
                        if m.status == "confirmed")
        unconfirmed = sum(1 for v in baseline.vendors for m in v.models
                          if m.status == "unconfirmed")
        summary = f"模型状态汇总：confirmed={confirmed} unconfirmed={unconfirmed}"
        print(f"vendors: {summary}，进表 {len(rows)} 行")
        if not rows:
            print("vendors: 报告为空：没有任何已确认模型，检查上方各模型未确认原因")
        if run_failures or failed:
            # 当日异常摘要告警：提取失败/公告源不可用/单厂商异常（静默缺口，spec §7）
            content_lines = [f"{date} 厂商新鲜度当日异常："]
            if run_failures:
                content_lines.append("暂态失败（详见 Actions 日志，次日自动重试）：")
                content_lines += [f"- {item}" for item in run_failures]
            if failed:
                content_lines.append(f"厂商处理失败（当日整体跳过）：{'、'.join(failed)}")
            content_lines.append(summary)
            link = run_link()
            if link:
                content_lines.append(link)
            send_alert(client, "大模型新鲜度厂商异常", "\n".join(content_lines))
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        report = args.reports_dir / f"各大厂商大模型新鲜度-{date}.md"
        report.write_text(render_markdown(date, rows), encoding="utf-8")
        report.with_suffix(".json").write_text(render_json(date, rows), encoding="utf-8")
        pending_rows = pending_rows_from_baseline(config, baseline)
        if pending_rows or failed:
            # 有错才有：未确认模型明细（-pending 后缀），随 reports 目录一并提交
            pending_report = args.reports_dir / f"各大厂商大模型新鲜度-{date}-pending.md"
            pending_report.write_text(
                render_pending_markdown(date, pending_rows, failed), encoding="utf-8")
            print(f"vendors: 未决明细 -> {pending_report}")
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
