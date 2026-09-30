import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from free_model_spider.core.diff import DiffResult, diff_models
from free_model_spider.core.intro import build_intro
from free_model_spider.core.report import IndexEntry, render_report, update_readme_index
from free_model_spider.core.snapshot import (
    find_latest_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from free_model_spider.llm.client import build_llm_client
from free_model_spider.search import build_search_provider
from free_model_spider.sources import available_sources, create_source
from free_model_spider.sources.base import sort_records

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _git_commit(date: str, data_dir: Path, reports_dir: Path, readme: Path) -> None:
    subprocess.run(["git", "add", str(data_dir), str(reports_dir), str(readme)],
                   check=True)
    result = subprocess.run(
        ["git", "commit", "-m", f"docs: 免费模型日报 {date} [skip ci]"],
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
    args = parser.parse_args(argv)

    date = args.date or datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    client = httpx.Client(timeout=60)
    names = args.sources.split(",") if args.sources else available_sources()
    search = build_search_provider(client)
    llm = build_llm_client(client)
    entries: list[IndexEntry] = []

    for name in names:
        source = create_source(name, client)
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
        entries.append(IndexEntry(
            date=date, source=name, display=source.display_name,
            added=len(diff.added), removed=len(diff.removed),
            path=report_rel.as_posix(),
        ))
        print(f"{name}: 免费 {len(records)}（+{len(diff.added)} / -{len(diff.removed)}）"
              f" -> {report_rel}")

    update_readme_index(args.readme, entries)
    if not args.no_commit:
        _git_commit(date, args.data_dir, args.reports_dir, args.readme)
    return 0
