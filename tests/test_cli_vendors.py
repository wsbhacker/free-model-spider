"""fms vendors v2 编排测试：官网公告爬取主通道 + bootstrap 种子 + 目录审计。

stub LLM + respx 公告列表页/文章页/目录 API；复刻 anthropic(sitemap)/
deepseek(headings) 的真实形态做 fixture（redesign spec §4）。
"""

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

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
UPDATES_URL = "https://api-docs.z.ai/updates"
SITEMAP_URL = "https://www.z-ai.com/sitemap.xml"
BLOG_URL = "https://www.z-ai.com/blog/glm-5-4"

CONFIG_YAML = f"""\
stale_days: 45
vendors:
  - id: zai
    name: 智谱 AI
    official_domains: [z.ai, z-ai.com]
    directory_slugs: {{ openrouter: [z-ai], modelsdev: [zai] }}
    announcements:
      - {{ name: 更新日志, url: "{UPDATES_URL}", mode: headings }}
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
    directory_slugs: {{ openrouter: [openai] }}
    # 无公告源：目录审计 + override 兜底
    lines:
      - key: flagship
        name: GPT 主线
        match: ['gpt-\\d']
"""

UPDATES_HTML = """<html><body>
<h1>Change Log</h1>
<h2>Date: 2026-10-20</h2>
<h3>GLM-5.4 Release</h3><p>new flagship</p>
<h2>Date: 2026-10-18</h2>
<h3>GLM-5.4-Flash Release</h3><p>new sub</p>
</body></html>"""

ARTICLE_HTML = "<html><body><p>智谱正式发布 GLM-5.4：于 2026-10-20 正式发布。</p></body></html>"

ARTICLE_JSON = (
    '{"model": "GLM-5.4", "release_date": "2026-10-20", '
    '"evidence": ["页面原文：GLM-5.4 于 2026-10-20 正式发布"], "reason": ""}'
)
FLASH_ARTICLE_JSON = (
    '{"model": "GLM-5.4-Flash", "release_date": "2026-10-18", '
    '"evidence": ["页面原文：GLM-5.4-Flash 于 2026-10-18 正式发布"], "reason": ""}'
)
IGNORE_JSON = '{"category": "ignore", "reason": "公司融资新闻，非模型发布"}'
INSUFFICIENT_JSON = (
    '{"model": null, "release_date": null, "evidence": [], '
    '"reason": "页面原文未出现发布日期"}'
)

REPORT_MD = "reports/各大厂商大模型新鲜度-2026-10-29.md"
REPORT_JSON = "reports/各大厂商大模型新鲜度-2026-10-29.json"


def _dir_payload(ids: list[str]) -> dict:
    return {"data": [{"id": i} for i in ids]}


def _init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    for args in (["init"], ["config", "user.name", "tester"],
                 ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text("# free-model-spider\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "vendor_releases.yml").write_text(CONFIG_YAML, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)


def _clean_env(monkeypatch):
    for var in ("GITHUB_ACTIONS", "LLM_API_KEY", "BRAVE_API_KEY", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


class FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0)


def _stub_llm(monkeypatch, llm):
    monkeypatch.setattr("free_model_spider.cli.build_llm_client", lambda client: llm)


def _stub_no_llm(monkeypatch):
    monkeypatch.setattr("free_model_spider.cli.build_llm_client", lambda client: None)


def _mock_offline(monkeypatch, updates_html=UPDATES_HTML):
    """目录两源 + 公告列表页 + 文章页的 respx 打桩。"""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=_dir_payload(["z-ai/glm-5.3"])))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(UPDATES_URL).mock(
        return_value=httpx.Response(200, text=updates_html))
    respx.get(BLOG_URL).mock(
        return_value=httpx.Response(200, text=ARTICLE_HTML))


def _zai_seeded(state_lines=None, seen=None) -> Baseline:
    """openai 不触发（目录条目已见）；zai 给定线状态。"""
    return Baseline(
        date="2026-10-28",
        vendors=[
            VendorState(id="zai", seen_announcement_urls=seen or [],
                        lines=state_lines or [LineState(key="flagship"),
                                              LineState(key="sub")]),
            VendorState(
                id="openai",
                seen_directory_ids=["openai/gpt-6.1"],
                lines=[LineState(
                    key="flagship", model="GPT-6.1", release_date="2026-10-20",
                    url="https://openai.com/gpt-6-1",
                    last_verified="2026-10-28", status="ok",
                )],
            ),
        ],
    )


# ---------- 场景①：bootstrap——空基线从列表页种子初始化（每线 1 次提取） ----------


@respx.mock
def test_bootstrap_seeds_each_line_from_newest_gated_listing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    llm = FakeLLM([ARTICLE_JSON, FLASH_ARTICLE_JSON])  # flagship + sub 各一次提取
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    # 恰好 2 次 LLM 调用（每线一次文章页提取），而非 v1 的 329 次 classify
    assert len(llm.calls) == 2
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    flagship, sub = zai.lines
    assert (flagship.model, flagship.release_date, flagship.status) == (
        "GLM-5.4", "2026-10-20", "ok")
    assert (sub.model, sub.release_date, sub.status) == (
        "GLM-5.4-Flash", "2026-10-18", "ok")
    # 全部列表条目入 seen（url::标题 复合键）：次日零重放
    assert len(zai.seen_announcement_urls) == 2
    assert all("::" in key for key in zai.seen_announcement_urls)
    assert "GLM-5.4" in Path(REPORT_MD).read_text("utf-8")

    # 次日重跑：seen 齐全、无新条目 → 零 LLM 调用，幂等
    llm2 = FakeLLM([])
    _stub_llm(monkeypatch, llm2)
    assert main(["vendors", "--date", "2026-10-30", "--no-commit"]) == 0
    assert llm2.calls == []


# ---------- 场景②：增量——已见 seen 之外的新公告条目触发裁决 ----------


@respx.mock
def test_incremental_new_announcement_updates_line(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    # 旧列表只有老条目（已入 seen）；新列表多出 GLM-5.4 条目
    old_html = UPDATES_HTML.replace(
        '<h2>Date: 2026-10-20</h2>\n<h3>GLM-5.4 Release</h3><p>new flagship</p>\n', '')
    baseline = _zai_seeded(
        state_lines=[LineState(
            key="flagship", model="GLM-5.3", release_date="2026-09-24",
            url="https://z.ai/blog/glm-5-3", last_verified="2026-10-28", status="ok",
        ), LineState(key="sub")],
        seen=[f"{UPDATES_URL}::glm-5.4-flash release"],
    )
    save_baseline(baseline, baseline_path(Path("data"), "2026-10-28"))
    llm = FakeLLM([ARTICLE_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert len(llm.calls) == 1  # 只对未见过的新条目提取
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    flagship = zai.lines[0]
    assert flagship.model == "GLM-5.4"
    assert flagship.status == "ok"
    assert len(zai.seen_announcement_urls) == 2  # 老条目 + 新条目
    assert flagship.reason.startswith("日期前进 2026-09-24 → 2026-10-20")


# ---------- 场景③：门筛未命中的公告走 LLM 归类，ignore 入 seen 不再重试 ----------


@respx.mock
def test_uncategorized_announcement_classified_ignore_enters_seen(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    # 老条目已入 seen；新条目（融资新闻）门筛零命中 → classify → ignore 终态入 seen
    html = """<html><body>
<h2>Date: 2026-10-10</h2>
<h3>old seeded post</h3><p>old</p>
<h2>Date: 2026-10-19</h2>
<h3>Z.ai completes Series C funding</h3><p>company news</p>
</body></html>"""
    save_baseline(
        _zai_seeded(seen=[f"{UPDATES_URL}::old seeded post"]),
        baseline_path(Path("data"), "2026-10-28"),
    )
    llm = FakeLLM([IGNORE_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch, updates_html=html)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert len(llm.calls) == 1  # 门筛零命中 → 1 次 classify，无提取
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert len(zai.seen_announcement_urls) == 2  # ignore 即终态入 seen
    assert all(line.status == "pending" for line in zai.lines)


# ---------- 场景④：提取失败 → 条目不入 seen（次日重试），线 pending 计数 ----------


@respx.mock
def test_extraction_failure_keeps_entry_unseen_and_line_pending(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    save_baseline(_zai_seeded(), baseline_path(Path("data"), "2026-10-28"))
    llm = FakeLLM([INSUFFICIENT_JSON, INSUFFICIENT_JSON, INSUFFICIENT_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert zai.lines[0].status == "pending"
    assert zai.lines[0].pending_days == 1
    assert zai.seen_announcement_urls == []  # 无终态结论 → 次日重试


# ---------- 场景⑤：公告列表抓取失败 → 降级沿用昨日基线，不崩 ----------


@respx.mock
def test_listing_fetch_failure_degrades_to_previous_baseline(tmp_path, monkeypatch,
                                                             capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    baseline = _zai_seeded(
        state_lines=[LineState(
            key="flagship", model="GLM-5.3", release_date="2026-09-24",
            url="https://z.ai/blog/glm-5-3", last_verified="2026-10-28", status="ok",
        ), LineState(key="sub")],
        seen=[f"{UPDATES_URL}::glm-5.4-flash release"],
    )
    save_baseline(baseline, baseline_path(Path("data"), "2026-10-28"))
    _stub_no_llm(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=_dir_payload([])))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(UPDATES_URL).mock(return_value=httpx.Response(500))

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    out = capsys.readouterr().out
    assert "公告列表抓取降级" in out
    md = Path(REPORT_MD).read_text("utf-8")
    assert "[GLM-5.3](https://z.ai/blog/glm-5-3)" in md  # 昨日基线照常出数


# ---------- 场景⑥：bootstrap 时 LLM 未配置 → 不入 seen，线保持 pending ----------


@respx.mock
def test_bootstrap_without_llm_keeps_seen_empty_for_retry(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _stub_no_llm(monkeypatch)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert zai.seen_announcement_urls == []  # 待 LLM 可用后整体重试 bootstrap
    assert all(line.status == "pending" for line in zai.lines)


# ---------- 场景⑦：pending 满 7 天冻结 → 恰好一次告警（v1 状态机不变） ----------


@respx.mock
def test_freeze_transition_sends_single_alert(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _stub_no_llm(monkeypatch)
    _mock_offline(monkeypatch)
    save_baseline(
        Baseline(
            date="2026-10-28",
            vendors=[
                VendorState(
                    id="zai",
                    seen_announcement_urls=[f"{UPDATES_URL}::glm-5.4-flash release"],  # 已见过 → 无新条目可裁决
                    lines=[
                        LineState(
                            key="flagship", status="pending", pending_days=7,
                            release_date="2026-10-25", reason="裁决缺失",
                        ),
                        LineState(
                            key="sub", status="pending", pending_days=1,
                            reason="裁决缺失",
                        ),
                    ],
                ),
                VendorState(
                    id="openai",
                    seen_directory_ids=["openai/gpt-6.1"],
                    lines=[LineState(
                        key="flagship", model="GPT-6.1", release_date="2026-10-20",
                        url="https://openai.com/gpt-6-1",
                        last_verified="2026-10-28", status="ok",
                    )],
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
    assert len(alerts) == 1  # 恰好一次（flagship 满 7 天）
    _, title, content = alerts[0]
    assert title == "大模型新鲜度厂商冻结"
    assert "智谱 AI" in content and "GLM 主线" in content

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = next(v for v in saved.vendors if v.id == "zai")
    assert zai.lines[0].status == "stale-baseline"
    assert zai.lines[1].status == "pending"  # 未满期不冻结
    openai = next(v for v in saved.vendors if v.id == "openai")
    assert openai.lines[0].status == "ok"
    assert openai.lines[0].last_verified == "2026-10-29"  # 每日收尾重臂


# ---------- 场景⑧：无公告源厂商——目录新条目审计提示 + ok 线照常出表 ----------


@respx.mock
def test_vendor_without_announcements_audits_directory_entries(tmp_path, monkeypatch,
                                                               capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _stub_no_llm(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(
            200, json=_dir_payload(["openai/gpt-6.1", "openai/gpt-6.2"])))
    respx.get(MODELS_DEV_URL).mock(return_value=httpx.Response(200, json={}))
    save_baseline(_zai_seeded(), baseline_path(Path("data"), "2026-10-28"))

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    out = capsys.readouterr().out
    assert "无公告源配置" in out
    assert "目录新条目 1 个" in out  # gpt-6.2 新、gpt-6.1 已见
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    openai = next(v for v in saved.vendors if v.id == "openai")
    assert "openai/gpt-6.2" in openai.seen_directory_ids  # 审计后入 seen，不重复提示
    md = Path(REPORT_MD).read_text("utf-8")
    assert "[GPT-6.1](https://openai.com/gpt-6-1)" in md


# ---------- 场景⑨：提交链路（沿用 v1：data+reports+readme，[skip ci]） ----------


@respx.mock
def test_commit_message_and_tracked_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    llm = FakeLLM([ARTICLE_JSON, FLASH_ARTICLE_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29"]) == 0  # 默认提交
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         check=True, capture_output=True, text=True).stdout
    assert log.splitlines()[0].endswith("docs: 大模型新鲜度日报 2026-10-29 [skip ci]")
    tracked = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files"],
        check=True, capture_output=True, text=True).stdout
    assert "data/vendor-releases/2026-10-29.json" in tracked
    assert "reports/各大厂商大模型新鲜度-2026-10-29.md" in tracked
    readme = Path("README.md").read_text("utf-8")
    assert FMS_VENDORS_START in readme
