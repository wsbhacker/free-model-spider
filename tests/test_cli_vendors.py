"""fms vendors 子命令编排测试：stub LLM/search + respx 目录 API（brief Step 1 五类场景）。"""

import json
import subprocess
from pathlib import Path

import httpx
import respx

from free_model_spider.cli import main
from free_model_spider.core.vendor_report import FMS_VENDORS_START
from free_model_spider.core.vendor_stats import (
    Baseline,
    LineState,
    VendorState,
    baseline_path,
    load_baseline,
    save_baseline,
)
from free_model_spider.search.base import SearchProvider, SearchResult

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
PAGE_URL = "https://z.ai/blog/glm-5-4"

CONFIG_YAML = """\
stale_days: 45
vendors:
  - id: zai
    name: 智谱 AI
    official_domains: [z.ai]
    directory_slugs: { openrouter: [z-ai], modelsdev: [zai] }
    lines:
      - key: flagship
        name: GLM 主线
        match: ['glm-\\d']
        exclude: [flash, air]
      - key: sub
        name: GLM Flash
        match: ['glm-\\d[\\d.]*-?(flash|flashx)']
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: { openrouter: [openai] }
    lines:
      - key: flagship
        name: GPT 主线
        match: ['gpt-\\d']
"""

CLASSIFY_FLAGSHIP_JSON = '{"category": "flagship", "reason": "主线新模型"}'
IGNORE_JSON = '{"category": "ignore", "reason": "量化挂靠变体"}'
EXTRACT_JSON = (
    '{"model": "GLM-5.4", "release_date": "2026-10-20", '
    '"source_urls": ["https://z.ai/blog/glm-5-4"], '
    '"evidence": ["GLM-5.4 于 2026-10-20 正式发布 https://z.ai/blog/glm-5-4"], '
    '"reason": ""}'
)
CONFIRM_JSON = (
    '{"model": "GLM-5.4", "release_date": "2026-10-20", '
    '"evidence": ["页面原文：GLM-5.4 于 2026-10-20 正式发布"], "reason": ""}'
)
CONFIRM_NULL_JSON = (
    '{"model": "GLM-5.4", "release_date": null, "evidence": [], '
    '"reason": "页面原文未出现发布日期"}'
)
INSUFFICIENT_JSON = (
    '{"model": null, "release_date": null, "source_urls": [], "evidence": [], '
    '"reason": "搜索结果未提及发布日期"}'
)
SUB_PAGE_URL = "https://z.ai/blog/glm-5-4-flash"
EXTRACT_SUB_JSON = (
    '{"model": "GLM-5.4-Flash", "release_date": "2026-10-18", '
    '"source_urls": ["https://z.ai/blog/glm-5-4-flash"], '
    '"evidence": ["GLM-5.4-Flash 于 2026-10-18 正式发布 https://z.ai/blog/glm-5-4-flash"], '
    '"reason": ""}'
)
CONFIRM_SUB_JSON = (
    '{"model": "GLM-5.4-Flash", "release_date": "2026-10-18", '
    '"evidence": ["页面原文：GLM-5.4-Flash 于 2026-10-18 正式发布"], "reason": ""}'
)
SEARCH_RESULTS = [
    SearchResult(title="智谱发布 GLM-5.4", url=PAGE_URL, snippet="GLM-5.4 于 2026-10-20 正式发布。")
]

REPORT_MD = "reports/各大厂商大模型新鲜度-2026-10-29.md"
REPORT_JSON = "reports/各大厂商大模型新鲜度-2026-10-29.json"


def _dir_payload(ids: list[str]) -> dict:
    return {"data": [{"id": i} for i in ids]}


FULL_DIR = _dir_payload(["z-ai/glm-5.4", "z-ai/glm-5.5-4bit", "openai/gpt-6.1"])
EMPTY_DIR: dict = {}


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, results=None):
        self.results = results or []
        self.queries: list[str] = []

    def search(self, query, count=5):
        self.queries.append(query)
        return self.results


class FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0)


def _git(repo: Path, *args: str):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "README.md").write_text("# free-model-spider\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "vendor_releases.yml").write_text(CONFIG_YAML, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")


def _clean_env(monkeypatch):
    for var in ("GITHUB_ACTIONS", "LLM_API_KEY", "BRAVE_API_KEY", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


def _stub_none(monkeypatch):
    monkeypatch.setattr("free_model_spider.cli.build_llm_client", lambda client: None)
    monkeypatch.setattr("free_model_spider.cli.build_search_provider", lambda client: None)


def _stub(monkeypatch, llm, search):
    monkeypatch.setattr("free_model_spider.cli.build_llm_client", lambda client: llm)
    monkeypatch.setattr("free_model_spider.cli.build_search_provider", lambda client: search)


def _mock_dirs(openrouter_payload, modelsdev_payload):
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=openrouter_payload)
    )
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=modelsdev_payload)
    )


def make_event_baseline() -> Baseline:
    """事件日前一日基线：zai 旗舰 ok（旧版本）、sub 空 pending；openai ok 且条目已见。"""
    return Baseline(
        date="2026-10-28",
        vendors=[
            VendorState(
                id="zai",
                seen_directory_ids=["z-ai/glm-5.3", "zai:glm-5.3-flash"],
                lines=[
                    LineState(
                        key="flagship", model="GLM-5.3", release_date="2026-09-24",
                        url="https://z.ai/blog/glm-5-3",
                        last_verified="2026-10-28", status="ok",
                    ),
                    LineState(key="sub"),
                ],
            ),
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-6.1"],
                lines=[
                    LineState(
                        key="flagship", model="GPT-6.1", release_date="2026-10-20",
                        url="https://openai.com/gpt-6-1",
                        last_verified="2026-10-28", status="ok",
                    ),
                ],
            ),
        ],
    )


# 事件日 stub 应答序列：classify glm-5.4 → flagship；classify 4bit 挂靠 → ignore；
# extract flagship → 有效；confirm → 一致；extract sub（无新条目）→ 材料不足。
EVENT_REPLIES = [
    CLASSIFY_FLAGSHIP_JSON,
    IGNORE_JSON,
    EXTRACT_JSON,
    CONFIRM_JSON,
    INSUFFICIENT_JSON,
]


def _event_setup(repo: Path, monkeypatch, replies: list[str],
                 baseline: Baseline | None = None) -> tuple[FakeLLM, respx.Route]:
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)  # 跳过 search 1.1s 限速
    llm = FakeLLM(replies)
    _stub(monkeypatch, llm, FakeSearch(SEARCH_RESULTS))
    _mock_dirs(FULL_DIR, {})
    confirm_route = respx.get(PAGE_URL).mock(  # 二次确认抓取的官方公告页
        return_value=httpx.Response(200, text="智谱正式发布 GLM-5.4：于 2026-10-20 正式发布。")
    )
    save_baseline(baseline or make_event_baseline(),
                  baseline_path(Path("data"), "2026-10-28"))
    return llm, confirm_route


# ---------- 场景①：首日 + 无 LLM/search → 空表 + 基线全 pending ----------


@respx.mock
def test_first_day_without_keys_produces_empty_report_and_pending_baseline(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    _stub_none(monkeypatch)
    _mock_dirs(FULL_DIR, {"zai": {"models": {"glm-5.4-flash": {}}}})

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    md = Path(REPORT_MD).read_text("utf-8")
    assert "| 厂商 | 模型 | 发布日 | 天数 |" in md
    assert "|---|---|---|---|" in md
    assert "GLM" not in md  # 空表：无任何模型行
    data = json.loads(Path(REPORT_JSON).read_text("utf-8"))
    assert data["models"] == []
    readme = Path("README.md").read_text("utf-8")
    assert FMS_VENDORS_START in readme
    assert "### 厂商新鲜度榜" in readme
    assert "> 更新于 2026-10-29" in readme
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    assert [v.id for v in saved.vendors] == ["zai", "openai"]
    for vendor in saved.vendors:
        for line in vendor.lines:
            assert line.status == "pending"
            assert line.model is None
            assert line.pending_days == 1  # 降级裁决走了 apply_judgment(None) 计数


# ---------- 场景②：事件日 → classify/extract/confirm 全通 → 基线更新 + 报告含行 ----------


@respx.mock
def test_event_day_updates_baseline_report_and_corroboration(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    llm, _ = _event_setup(repo, monkeypatch, EVENT_REPLIES)
    commits: list = []
    monkeypatch.setattr(
        "free_model_spider.cli._git_commit", lambda *a, **k: commits.append((a, k))
    )

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert commits == []  # --no-commit 下不调 git
    assert len(llm.calls) == 5  # 2 classify + 1 extract + 1 confirm + 1 extract(sub)

    md = Path(REPORT_MD).read_text("utf-8")
    assert (
        "| 智谱 AI | [GLM-5.4](https://z.ai/blog/glm-5-4) "
        "[⧉](https://openrouter.ai/z-ai/glm-5.4) | 2026-10-20 | 9 |" in md
    )
    assert "| OpenAI | [GPT-6.1](https://openai.com/gpt-6-1) | 2026-10-20 | 9 |" in md
    data = json.loads(Path(REPORT_JSON).read_text("utf-8"))
    assert data["models"][0] == {
        "vendor": "智谱 AI", "name": "GLM-5.4",
        "url": PAGE_URL, "release_date": "2026-10-20",
    }
    readme = Path("README.md").read_text("utf-8")
    assert "[⧉](https://openrouter.ai/z-ai/glm-5.4)" in readme

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    flagship, sub = zai.lines[0], zai.lines[1]
    assert flagship.model == "GLM-5.4"
    assert flagship.release_date == "2026-10-20"
    assert flagship.url == PAGE_URL
    assert flagship.status == "ok"
    assert flagship.reason.startswith("日期前进 2026-09-24 → 2026-10-20")
    assert "ignore 审计：z-ai/glm-5.5-4bit（量化挂靠变体）" in flagship.reason
    assert "z-ai/glm-5.4" in zai.seen_directory_ids  # 终态条目入 seen
    assert "z-ai/glm-5.5-4bit" in zai.seen_directory_ids  # ignore 亦终态
    assert sub.status == "pending"  # sub 线提取材料不足 → pending 计数
    assert sub.pending_days == 1
    assert sub.model is None


@respx.mock
def test_event_day_commit_message(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _event_setup(repo, monkeypatch, EVENT_REPLIES)

    assert main(["vendors", "--date", "2026-10-29"]) == 0  # 默认提交
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], check=True,
        capture_output=True, text=True,
    ).stdout
    assert log.splitlines()[0].endswith("docs: 大模型新鲜度日报 2026-10-29 [skip ci]")
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "data/vendor-releases"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "data/vendor-releases/2026-10-29.json" in tracked
    # core.quotepath 默认把非 ASCII 路径转义，关闭后断言原始中文文件名
    reports_tracked = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files", "reports"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "reports/各大厂商大模型新鲜度-2026-10-29.md" in reports_tracked


# ---------- 场景③：二次确认失败 → 线 pending、不进表 ----------


@respx.mock
def test_confirm_failure_keeps_line_pending_and_out_of_table(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    # zai 旗舰线从未确认（空 pending）：提取有效但二次确认失败 → 不写基线、不进表。
    # seen 必须含 4bit 挂靠条目：否则它会成为第二个粗筛候选，LLM 应答队列整体错位，
    # CONFIRM_NULL_JSON 会被 extract 吃掉、confirm_from_page 根本不执行（评审 Important 1）。
    first_confirm_baseline = Baseline(
        date="2026-10-28",
        vendors=[
            VendorState(
                id="zai",
                seen_directory_ids=["z-ai/glm-5.3", "z-ai/glm-5.5-4bit"],
                lines=[LineState(key="flagship"), LineState(key="sub")],
            ),
            make_event_baseline().vendors[1],  # openai：ok 且条目已见 → 不触发
        ],
    )
    llm, confirm_route = _event_setup(
        repo, monkeypatch,
        [CLASSIFY_FLAGSHIP_JSON, EXTRACT_JSON, CONFIRM_NULL_JSON, INSUFFICIENT_JSON],
        baseline=first_confirm_baseline,
    )

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert len(llm.calls) == 4  # classify + extract + confirm（确认未过）+ extract(sub)
    assert confirm_route.call_count == 1  # 确认环节确实抓取过官方公告页

    md = Path(REPORT_MD).read_text("utf-8")
    assert "GLM-5.4" not in md  # 从未确认的线不进表
    assert "| OpenAI | [GPT-6.1](https://openai.com/gpt-6-1) | 2026-10-20 | 9 |" in md

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    flagship = zai.lines[0]
    assert flagship.status == "pending"
    assert flagship.model is None  # 确认失败：基线值不被写入（原空线保持 None）
    assert flagship.url is None
    assert flagship.pending_days == 1
    assert "z-ai/glm-5.4" not in zai.seen_directory_ids  # 无终态结论 → 次日重触发


# ---------- 场景④：pending 满 7 天冻结 → 恰好一次 send_alert ----------

@respx.mock
def test_freeze_transition_sends_single_alert(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    _stub_none(monkeypatch)
    _mock_dirs(EMPTY_DIR, {})
    # 前一日基线：zai 旗舰 pending 已满 7 天；sub 为曾确认过的线复核转 pending
    # （last_verified 新近）——两线参考日都新鲜，zai 不触发 stale，旗舰走
    # "未触发厂商线 finalize_state 冻结"路径（spec §6.2 改任一线超窗后，
    # 任一线超窗都会触发复核，故这里两线都须保持新鲜）；openai ok 新鲜。
    save_baseline(
        Baseline(
            date="2026-10-28",
            vendors=[
                VendorState(
                    id="zai",
                    seen_directory_ids=["z-ai/glm-5.3"],
                    lines=[
                        LineState(
                            key="flagship", status="pending", pending_days=7,
                            release_date="2026-10-25", reason="裁决缺失",
                        ),
                        LineState(
                            key="sub", model="GLM-5.3-Flash",
                            release_date="2026-09-01",
                            url="https://z.ai/blog/glm-5-3-flash",
                            last_verified="2026-10-28",
                            status="pending", pending_days=2, reason="裁决缺失",
                        ),
                    ],
                ),
                VendorState(
                    id="openai",
                    lines=[
                        LineState(
                            key="flagship", model="GPT-6.1", release_date="2026-10-20",
                            url="https://openai.com/gpt-6-1",
                            last_verified="2026-10-28", status="ok",
                        ),
                    ],
                ),
            ],
        ),
        baseline_path(Path("data"), "2026-10-28"),
    )
    alerts: list[tuple] = []
    monkeypatch.setattr(
        "free_model_spider.cli.send_alert",
        lambda client, title, content: alerts.append((client, title, content)),
    )

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert len(alerts) == 1  # 迁移那一刻只报一次
    _, title, content = alerts[0]
    assert title == "大模型新鲜度厂商冻结"
    assert "智谱 AI" in content
    assert "GLM 主线" in content
    assert "7" in content

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert zai.lines[0].status == "stale-baseline"
    assert zai.lines[0].pending_days == 7
    assert "冻结" in zai.lines[0].reason
    openai = next(v for v in saved.vendors if v.id == "openai")
    assert openai.lines[0].status == "ok"
    assert openai.lines[0].last_verified == "2026-10-29"  # 未冻结线收尾重臂


# ---------- 场景④b：厂商因其余线触发复核时，冻结线被跳过（零 LLM 调用） ----------


@respx.mock
def test_frozen_line_skipped_in_review_no_llm_calls(tmp_path, monkeypatch):
    """复核循环跳过 status='stale-baseline' 的线：不做 extract/classify，
    其余线照常（spec §7.2：冻结线不得每日烧配额）。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    # 应答恰好覆盖“classify glm-5.4 + sub 线 extract”：若冻结线未被跳过，
    # flagship 会先吃掉 INSUFFICIENT_JSON，sub 弹空列表 → IndexError → rc 1。
    llm = FakeLLM([CLASSIFY_FLAGSHIP_JSON, INSUFFICIENT_JSON])
    _stub(monkeypatch, llm, FakeSearch(SEARCH_RESULTS))
    _mock_dirs(FULL_DIR, {})
    save_baseline(
        Baseline(
            date="2026-10-28",
            vendors=[
                VendorState(
                    id="zai",
                    seen_directory_ids=[
                        "z-ai/glm-5.3", "zai:glm-5.3-flash", "z-ai/glm-5.5-4bit",
                    ],
                    lines=[
                        LineState(
                            key="flagship", model="GLM-5.3", release_date="2026-09-24",
                            url="https://z.ai/blog/glm-5-3",
                            last_verified="2026-10-28", status="stale-baseline",
                            reason="pending 连续 7 天未决，冻结为 stale-baseline",
                            pending_days=9,
                        ),
                        LineState(
                            key="sub", model="GLM-5.3-Flash", release_date="2026-10-01",
                            url="https://z.ai/blog/glm-5-3-flash",
                            last_verified="2026-10-28", status="ok",
                        ),
                    ],
                ),
                make_event_baseline().vendors[1],  # openai：ok 且条目已见 → 不触发
            ],
        ),
        baseline_path(Path("data"), "2026-10-28"),
    )

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert len(llm.calls) == 2  # 1 classify + 1 extract(sub)；冻结 flagship 零调用

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    flagship, sub = zai.lines[0], zai.lines[1]
    assert flagship.status == "stale-baseline"  # 冻结线原样保留，未被复核改动
    assert flagship.model == "GLM-5.3"
    assert flagship.release_date == "2026-09-24"
    assert flagship.last_verified == "2026-10-28"
    assert sub.status == "pending"  # 其余线照常裁决：sub 提取材料不足转 pending
    assert sub.pending_days == 1
    assert "z-ai/glm-5.4" in zai.seen_directory_ids  # 归类到冻结线（非 pending）→ 终态入 seen


# ---------- 场景⑥：配置给既有厂商新增产品线 → 基线补线，不再 KeyError ----------


@respx.mock
def test_new_line_added_to_existing_vendor_is_backfilled(tmp_path, monkeypatch):
    """yml 给既有厂商新增线而旧基线缺该线：运行时按配置顺序补 pending 线，
    复核循环不再 line_states[key] KeyError → 厂商永久失败（final-review F5）。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    llm = FakeLLM(EVENT_REPLIES)
    _stub(monkeypatch, llm, FakeSearch(SEARCH_RESULTS))
    _mock_dirs(FULL_DIR, {})
    respx.get(PAGE_URL).mock(  # 二次确认抓取的官方公告页
        return_value=httpx.Response(200, text="智谱正式发布 GLM-5.4：于 2026-10-20 正式发布。")
    )
    # 旧基线：zai 只有 flagship（对应 yml 后来新增 sub 线的场景）
    save_baseline(
        Baseline(
            date="2026-10-28",
            vendors=[
                VendorState(
                    id="zai",
                    seen_directory_ids=["z-ai/glm-5.3"],
                    lines=[
                        LineState(
                            key="flagship", model="GLM-5.3", release_date="2026-09-24",
                            url="https://z.ai/blog/glm-5-3",
                            last_verified="2026-10-28", status="ok",
                        ),
                    ],
                ),
                make_event_baseline().vendors[1],  # openai 完整
            ],
        ),
        baseline_path(Path("data"), "2026-10-28"),
    )

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert [line.key for line in zai.lines] == ["flagship", "sub"]  # 缺线已按配置顺序补齐
    sub = zai.lines[1]
    assert sub.status == "pending"  # 补的线随当日裁决走 pending
    assert sub.model is None
    flagship = zai.lines[0]
    assert flagship.status == "ok"  # 原有线不受影响，照常裁决
    assert flagship.model == "GLM-5.4"


# ---------- 场景⑤：同日重跑幂等 → 不重复判 LLM ----------


@respx.mock
def test_same_day_rerun_is_idempotent(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    # spec §6.2 改任一线超窗触发后：空 pending 线（纪元参考日）会每日触发复核，
    # 故首轮让 sub 线也裁决落定（ok + last_verified=today），重跑才零触发。
    _event_setup(
        repo, monkeypatch,
        [
            CLASSIFY_FLAGSHIP_JSON, IGNORE_JSON, EXTRACT_JSON, CONFIRM_JSON,
            EXTRACT_SUB_JSON, CONFIRM_SUB_JSON,
        ],
    )
    respx.get(SUB_PAGE_URL).mock(  # sub 线二次确认抓取的官方公告页
        return_value=httpx.Response(
            200, text="智谱正式发布 GLM-5.4-Flash：于 2026-10-18 正式发布。"
        )
    )
    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    llm2 = FakeLLM([])  # 二次运行的 stub：任何 LLM 调用都会 pop 空列表报错
    _stub(monkeypatch, llm2, FakeSearch([]))
    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert llm2.calls == []  # 当日基线已存在且无新事件 → 零 LLM 调用
    md_after = Path(REPORT_MD).read_text("utf-8")
    # 行内容不变（佐证 URL 只在裁决日重建，同日重跑无裁决则不附 ⧉，属预期语义）
    assert "| 智谱 AI | [GLM-5.4](https://z.ai/blog/glm-5-4) | 2026-10-20 | 9 |" in md_after
    assert "| OpenAI | [GPT-6.1](https://openai.com/gpt-6-1) | 2026-10-20 | 9 |" in md_after
