import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from free_model_spider.core.agent_backend import (
    AgentStructuralError,
    AgentTransientError,
    build_agent_backend,
)
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
from free_model_spider.core.vendor_agent import (
    Accepted,
    apply_accepted,
    build_bootstrap_query,
    build_daily_query,
    parse_bootstrap_answer,
    parse_daily_answer,
    slugify_name,
    validate_bootstrap,
    validate_daily,
)
from free_model_spider.core.vendor_config import load_config
from free_model_spider.core.vendor_report import (
    PendingLine,
    RejectedLine,
    render_json,
    render_markdown,
    render_pending_markdown,
    rows_from_baseline,
    update_readme_freshness,
)
from free_model_spider.core.vendor_state import (
    Baseline,
    LastCheck,
    ModelState,
    VendorState,
    baseline_path,
    find_latest_baseline,
    load_baseline,
    save_baseline,
    window_start,
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
        # daily / vendors 两个 workflow 共用 concurrency group 串行；排队的那个 checkout 的
        # 是触发时刻的 SHA，前一个已 push 则本地落后，先 rebase 到远端最新再 push
        subprocess.run(["git", "pull", "--rebase", "--autostash"], check=True)
        subprocess.run(["git", "push"], check=True)


def run_vendors(args) -> int:
    """fms vendors 编排 v4：Dify Agent 监测厂商新鲜度（spec 2026-11-02 §8）。

    daily（默认）：逐厂商问询 Agent → 校验 → 接收入库 / 拒收进 pending → 报告 +
    README + 当日基线 + 提交。暂态失败不改写模型状态（仅 last_check 记 transient，
    下次运行重试）；结构性错误（无基线 / 缺配置 / 鉴权失败）中止整个运行且不写当日
    基线，先告警再非零退出。yml 已移除厂商的状态块随基线复制保留，不提问不渲染。
    bootstrap：Agent 逐厂商生成草稿基线，不写报告、不改 README、不提交。
    """
    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60, follow_redirects=True)
    if args.vendor and not args.bootstrap:
        print("vendors: --vendor 仅在与 --bootstrap 同用时有效")
        return 1
    failed: list[str] = []  # 未建基线 / 单厂商异常：退出码 1（spec §8.1 第 6 步）
    try:
        config = load_config(args.config)

        if args.bootstrap:
            # ---- bootstrap（spec §8.2）：整体草稿要求无任何基线；--vendor 只替换该厂商块 ----
            if not args.vendor and any(
                    (args.data_dir / "vendor-releases").glob("*.json")):
                print("vendors: 已存在基线；重做单厂商用 --vendor <id>，"
                      "整体重做请先手动删除 data/vendor-releases/ 下的基线文件")
                return 1
            targets = ([v for v in config.vendors if v.id == args.vendor]
                       if args.vendor else list(config.vendors))
            if args.vendor and not targets:
                print(f"vendors: --vendor {args.vendor} 不在 {args.config} 中")
                return 1
            backend = build_agent_backend(client)
            base = Baseline(date=date)
            if args.vendor:
                latest = find_latest_baseline(args.data_dir, date)
                if latest is not None:
                    base = load_baseline(latest)
            base.date = date
            transient_ids: list[str] = []
            drafts: dict[str, VendorState] = {}
            for vendor in targets:
                try:
                    query = build_bootstrap_query(
                        vendor.name, vendor.aliases, vendor.official_domains, date)
                    answer = parse_bootstrap_answer(backend.ask(query).text)
                except AgentTransientError as exc:
                    print(f"vendors: {vendor.id} bootstrap 暂态失败：{exc}"
                          "（写入空 models 块，请重试）")
                    drafts[vendor.id] = VendorState(
                        id=vendor.id,
                        last_check=LastCheck(date=date, status="transient",
                                             detail=str(exc)))
                    transient_ids.append(vendor.id)
                    continue
                if answer is None:
                    detail = "agent 回答不可解析（暂态，请重试）"
                    print(f"vendors: {vendor.id} bootstrap {detail}"
                          "（写入空 models 块）")
                    drafts[vendor.id] = VendorState(
                        id=vendor.id,
                        last_check=LastCheck(date=date, status="transient",
                                             detail=detail))
                    transient_ids.append(vendor.id)
                    continue
                accepted, rejected = validate_bootstrap(
                    answer, vendor.official_domains, date)
                # validate_bootstrap 不去重同批同 key、apply_accepted 无同 key 防护：
                # 草稿构造处自行按 key 去重（保留首个，丢弃后续重复条目）
                kept: list[Accepted] = []
                seen_keys: set[str] = set()
                for item in accepted:
                    key = slugify_name(item.name)
                    if key in seen_keys:
                        print(f"vendors: {vendor.id} 丢弃重复条目 {item.name}"
                              f"（key {key} 与本批已有条目相同）")
                        continue
                    seen_keys.add(key)
                    kept.append(item)
                models = [
                    ModelState(key=slugify_name(item.name), name=item.name,
                               release_date=item.release_date, url=item.url,
                               added_on=date)
                    for item in kept
                ]
                drafts[vendor.id] = VendorState(
                    id=vendor.id, models=models,
                    last_check=LastCheck(date=date, status="ok", detail="bootstrap"))
                print(f"[vendors] bootstrap {vendor.id}: "
                      f"接收 {len(kept)} / 拒收 {len(rejected)}")
                for item in kept:
                    print(f"  - {item.name} | {item.release_date} | {item.url}")
                for item in rejected:
                    print(f"  拒收 {item.name}：{item.reason}")
                if not kept:
                    print(f"vendors: 注意——{vendor.id} 引导回答为空清单，"
                          "已写入空 models 块，请人工审阅补充")
            base.vendors = [v for v in base.vendors if v.id not in drafts]
            base.vendors.extend(drafts[vendor.id] for vendor in targets)
            path = baseline_path(args.data_dir, date)
            save_baseline(base, path)
            print(f"vendors: bootstrap 草稿 -> {path}")
            return 1 if transient_ids else 0

        # ---- daily（spec §8.1）----
        latest = find_latest_baseline(args.data_dir, date)
        if latest is None:
            message = (f"{date} 无任何 schema 4 基线："
                       "请先运行 fms vendors --bootstrap 生成基线草稿")
            print(f"vendors: {message}")
            send_alert(client, "大模型新鲜度日报失败", f"{message}\n{run_link()}")
            return 1
        backend = build_agent_backend(client)
        baseline = load_baseline(latest)
        baseline.date = date
        states = {v.id: v for v in baseline.vendors}
        run_failures: list[PendingLine] = []  # 暂态明细：状态原样，下次运行重试
        pending_failed: list[PendingLine] = []  # 未建基线 / 单厂商异常明细
        rejected_lines: list[RejectedLine] = []
        new_model_lines: list[str] = []
        for vendor in config.vendors:
            state = states.get(vendor.id)
            if state is None:  # yml 有而基线无：不自动播种，人工 --bootstrap 补
                reason = (f"未建基线，请运行 "
                          f"fms vendors --bootstrap --vendor {vendor.id}")
                failed.append(f"{vendor.id} {reason}")
                pending_failed.append(PendingLine(vendor.name, reason))
                print(f"vendors: {vendor.id} {reason}，跳过提问")
                continue
            try:
                if not state.active_models():  # spec §7.1：无在表模型窗口回退 180 天，当日 -pending 记一行
                    run_failures.append(PendingLine(
                        vendor.name, "无在表模型，提问窗口回退 180 天"))
                window_start_day = window_start(state, date)
                query = build_daily_query(
                    vendor.name, vendor.aliases, vendor.official_domains,
                    date, window_start_day, state.active_models())
                result = backend.ask(query)
                answer = parse_daily_answer(result.text)
                if answer is None:  # 结构不对整厂商记暂态（spec §7.1）
                    detail = "agent 回答不可解析（暂态，下次运行重试）"
                    state.last_check = LastCheck(date=date, status="transient",
                                                 detail=detail)
                    run_failures.append(PendingLine(vendor.name, detail))
                    continue
                accepted, rejected, ignored = validate_daily(
                    answer, state, vendor.official_domains, window_start_day, date)
                added = apply_accepted(state, accepted, date)
                state.last_check = LastCheck(
                    date=date, status="ok",
                    detail=f"新增 {len(added)} 个" if added else "无新模型")
                rejected_lines += [
                    RejectedLine(vendor.name, item.name, item.reason, result.text)
                    for item in rejected]
                for item in accepted:
                    line = (f"- {vendor.name} / {item.name} / "
                            f"{item.release_date} / {item.url}")
                    if item.supersedes_key:
                        old = next((m for m in state.models
                                    if m.key == item.supersedes_key), None)
                        if old is not None:
                            line += f"（取代 {old.name}）"
                    new_model_lines.append(line)
                print(f"[vendors] {vendor.id}: 接收 {len(accepted)} / 拒收 "
                      f"{len(rejected)} / 忽略 {len(ignored)}"
                      f"（窗口 {window_start_day}）")
            except AgentStructuralError:
                # 鉴权/配置类失败所有厂商必然同样命中：中止整个运行，不写基线不提交
                raise
            except AgentTransientError as exc:
                state.last_check = LastCheck(date=date, status="transient",
                                             detail=str(exc))
                run_failures.append(
                    PendingLine(vendor.name, f"{exc}（暂态，下次运行重试）"))
                continue
            except Exception as exc:  # 单厂商隔离：一家失败只损失该家（沿用 v3）
                failed.append(vendor.id)
                pending_failed.append(PendingLine(vendor.name, repr(exc)[:200]))
                print(f"vendors: {vendor.id} 处理失败，跳过该厂商：{exc!r}")
                continue

        rows = rows_from_baseline(config, baseline, date)
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        report = args.reports_dir / f"各大厂商大模型新鲜度-{date}.md"
        report.write_text(render_markdown(date, rows), encoding="utf-8")
        report.with_suffix(".json").write_text(render_json(date, rows),
                                               encoding="utf-8")
        if pending_failed or run_failures or rejected_lines:
            pending_report = (args.reports_dir /
                              f"各大厂商大模型新鲜度-{date}-pending.md")
            pending_report.write_text(
                render_pending_markdown(date, pending_failed, run_failures,
                                        rejected_lines),
                encoding="utf-8")
            print(f"vendors: 未决明细 -> {pending_report}")
        update_readme_freshness(args.readme, date, rows)
        save_baseline(baseline, baseline_path(args.data_dir, date))
        print(f"vendors: 报告 -> {report}")
        if not args.no_commit:
            _git_commit(date, args.data_dir, args.reports_dir, args.readme,
                        message=f"docs: 大模型新鲜度日报 {date} [skip ci]")
        if new_model_lines:
            content = "\n".join([f"{date} 发现新模型：", *new_model_lines])
            link = run_link()
            if link:
                content += f"\n{link}"
            send_alert(client, "发现新模型", content)
        if pending_failed or run_failures or rejected_lines:
            content_lines = [f"{date} 厂商新鲜度当日异常："]
            content_lines += [f"- 处理异常：{item}" for item in failed]
            content_lines += [f"- 暂态失败：{line.vendor_name} {line.reason}"
                              for line in run_failures]
            content_lines += [f"- 拒收：{line.vendor_name} / {line.name}："
                              f"{line.reason}" for line in rejected_lines]
            link = run_link()
            if link:
                content_lines.append(link)
            send_alert(client, "大模型新鲜度厂商异常", "\n".join(content_lines))
    except Exception as exc:  # 顶层兜底：结构性/未捕获异常先告警再非零退出（spec §8.1 第 6 步）
        print(f"vendors: 处理失败：{exc!r}")
        send_alert(client, "大模型新鲜度日报失败",
                   f"{date} 大模型新鲜度日报失败：{exc!r}\n{run_link()}")
        return 1
    if failed:
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
    vendors.add_argument("--no-commit", action="store_true")
    vendors.add_argument("--config", type=Path, default=Path("data/vendors.yml"),
                         help="厂商身份配置（v4：仅 id/name/官方域名/别名）")
    vendors.add_argument("--bootstrap", action="store_true",
                         help="让 Agent 逐厂商生成基线草稿（不写报告、不提交）")
    vendors.add_argument("--vendor", default=None,
                         help="仅与 --bootstrap 同用：只引导/替换该厂商 id")
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
