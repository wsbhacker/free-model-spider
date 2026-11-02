"""fms vendors v4 编排测试：Dify Agent 问询 → 校验入库 → 报告/基线/提交 + bootstrap。

假 backend 按厂商显示名子串匹配回答；不触网（send_alert/build_agent_backend 均打桩）。
覆盖 spec §13 的 12 个集成用例。
"""

import json
import subprocess
from pathlib import Path

from free_model_spider.cli import main
from free_model_spider.core.agent_backend import (
    AgentAnswer,
    AgentStructuralError,
    AgentTransientError,
)
from free_model_spider.core.vendor_report import FMS_VENDORS_START
from free_model_spider.core.vendor_state import (
    Baseline,
    ModelState,
    VendorState,
    baseline_path,
    load_baseline,
    save_baseline,
)

DATE = "2026-11-02"
BASELINE_DATE = "2026-11-01"
REPORT_MD = f"reports/各大厂商大模型新鲜度-{DATE}.md"
PENDING_MD = f"reports/各大厂商大模型新鲜度-{DATE}-pending.md"

VENDORS_YAML = """\
vendors:
  - id: anthropic
    name: Anthropic
    official_domains: [anthropic.com]
    aliases: [Claude]
  - id: moonshot
    name: 月之暗面
    official_domains: [moonshot.cn, moonshot.ai, kimi.com]
    aliases: [Moonshot AI, Kimi]
"""

OPUS_6 = {
    "name": "Claude Opus 6",
    "release_date": "2026-11-01",
    "url": "https://www.anthropic.com/news/claude-opus-6",
    "supersedes": "claude-opus-5-5",
}
FAKE_9 = {
    "name": "Claude Fake 9",
    "release_date": "2026-11-01",
    "url": "https://evil.example.com/x",
}
EMPTY_DAILY = '{"new_models": []}'

BOOTSTRAP_ANTHROPIC = json.dumps({"models": [dict(OPUS_6, supersedes=None)]},
                                 ensure_ascii=False)
BOOTSTRAP_MOONSHOT = json.dumps(
    {"models": [{"name": "Kimi K3", "release_date": "2026-06-01",
                 "url": "https://www.moonshot.ai/news/kimi-k3"}]},
    ensure_ascii=False)


def _daily_answer(*models: dict) -> str:
    return json.dumps({"new_models": list(models)}, ensure_ascii=False)


def _baseline() -> Baseline:
    """既有基线：anthropic 一个 active 模型 claude-opus-5-5（2026-08-14）；
    moonshot 一个 active 模型 kimi-k3（yml 两家都在基线中，日常运行才不会
    触发用例④的「未建基线」路径；用例④自行覆写为只含 anthropic）。"""
    return Baseline(
        date=BASELINE_DATE,
        vendors=[
            VendorState(
                id="anthropic",
                models=[
                    ModelState(
                        key="claude-opus-5-5", name="Claude Opus 5.5",
                        release_date="2026-08-14",
                        url="https://www.anthropic.com/news/claude-opus-5-5",
                        added_on="2026-08-14",
                    ),
                ],
            ),
            VendorState(
                id="moonshot",
                models=[
                    ModelState(
                        key="kimi-k3", name="Kimi K3",
                        release_date="2026-06-01",
                        url="https://www.moonshot.ai/news/kimi-k3",
                        added_on="2026-06-01",
                    ),
                ],
            ),
        ],
    )


class FakeBackend:
    """按厂商显示名子串匹配回答：key = 厂商名子串 → 回答文本或异常实例。"""

    def __init__(self, answers: dict[str, str | Exception]):
        self.answers = answers
        self.queries: list[str] = []

    def ask(self, query: str) -> AgentAnswer:
        self.queries.append(query)
        for fragment, value in self.answers.items():
            if fragment in query:
                if isinstance(value, Exception):
                    raise value
                return AgentAnswer(text=value, usage=None)
        raise AssertionError(f"未匹配到厂商的 query：{query[:80]}")


def _stub_backend(monkeypatch, fake: FakeBackend) -> None:
    monkeypatch.setattr("free_model_spider.cli.build_agent_backend",
                        lambda client: fake)


def _capture_alerts(monkeypatch) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "free_model_spider.cli.send_alert",
        lambda client, title, content: alerts.append((title, content)),
    )
    return alerts


def _clean_env(monkeypatch) -> None:
    for var in ("GITHUB_ACTIONS", "ALERT_WEBHOOK_URL", "DIFY_API_BASE",
                "DIFY_API_TOKEN", "CF_ACCESS_CLIENT_ID",
                "CF_ACCESS_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for args in (["init"], ["config", "user.name", "tester"],
                 ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    (repo / "README.md").write_text("# free-model-spider\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "vendors.yml").write_text(VENDORS_YAML, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)


def _git_log_count(repo: Path) -> int:
    out = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         check=True, capture_output=True, text=True).stdout
    return len(out.splitlines())


def _setup(tmp_path, monkeypatch, *, baseline: bool = True) -> tuple[Path, FakeBackend]:
    """公共起点：临时 git 仓库 + 假 backend + 告警捕获；可选预置既有基线。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    fake = FakeBackend({"Anthropic": EMPTY_DAILY, "月之暗面": EMPTY_DAILY})
    _stub_backend(monkeypatch, fake)
    if baseline:
        save_baseline(_baseline(), baseline_path(Path("data"), BASELINE_DATE))
    return repo, fake


# ---------- ①：新模型入库、旧模型淘汰、发现新模型告警、日报提交 ----------


def test_new_model_added_old_retired_alert_and_commit(tmp_path, monkeypatch, capsys):
    repo, fake = _setup(tmp_path, monkeypatch)
    fake.answers["Anthropic"] = _daily_answer(OPUS_6)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE]) == 0  # 默认提交

    md = Path(REPORT_MD).read_text("utf-8")
    assert "Claude Opus 6" in md and "Claude Opus 5.5" not in md
    readme = Path("README.md").read_text("utf-8")
    assert FMS_VENDORS_START in readme
    assert "Claude Opus 6" in readme and "Claude Opus 5.5" not in readme
    saved = load_baseline(baseline_path(Path("data"), DATE))
    anthropic = next(v for v in saved.vendors if v.id == "anthropic")
    old, new = anthropic.models
    assert (old.key, old.retired_on, old.superseded_by) == (
        "claude-opus-5-5", DATE, "claude-opus-6")
    assert (new.key, new.added_on, new.retired_on) == ("claude-opus-6", DATE, None)
    new_alerts = [content for title, content in alerts if title == "发现新模型"]
    assert len(new_alerts) == 1
    assert "Claude Opus 5.5" in new_alerts[0]  # 取代了谁
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         check=True, capture_output=True, text=True).stdout
    assert log.splitlines()[0].endswith(f"docs: 大模型新鲜度日报 {DATE} [skip ci]")
    tracked = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files"],
        check=True, capture_output=True, text=True).stdout
    assert f"data/vendor-releases/{DATE}.json" in tracked
    assert ("[vendors] anthropic: 接收 1 / 拒收 0 / 忽略 0（窗口 2026-08-14）"
            in capsys.readouterr().out)


# ---------- ②：暂态失败——状态原样、-pending、异常告警、退出码 0 ----------


def test_transient_error_keeps_state_alerts_exit_zero(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    fake = FakeBackend({"Anthropic": AgentTransientError("超时"),
                        "月之暗面": EMPTY_DAILY})
    _stub_backend(monkeypatch, fake)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 0

    saved = load_baseline(baseline_path(Path("data"), DATE))
    anthropic = next(v for v in saved.vendors if v.id == "anthropic")
    assert [(m.key, m.retired_on) for m in anthropic.models] == [
        ("claude-opus-5-5", None)]
    assert anthropic.last_check is not None
    assert anthropic.last_check.status == "transient"
    pending = Path(PENDING_MD).read_text("utf-8")
    assert "下次运行重试" in pending and "Anthropic" in pending
    assert [t for t, _ in alerts] == ["大模型新鲜度厂商异常"]


# ---------- ③：拒收条目进 pending、不入库 ----------


def test_rejected_candidate_goes_to_pending_not_state(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    fake = FakeBackend({"Anthropic": _daily_answer(FAKE_9),
                        "月之暗面": EMPTY_DAILY})
    _stub_backend(monkeypatch, fake)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 0  # 仅拒收 → 0

    saved = load_baseline(baseline_path(Path("data"), DATE))
    anthropic = next(v for v in saved.vendors if v.id == "anthropic")
    assert [m.key for m in anthropic.models] == ["claude-opus-5-5"]  # 不入库
    pending = Path(PENDING_MD).read_text("utf-8")
    assert "证据域名非官方" in pending
    assert "Claude Fake 9" in pending  # agent 原文摘录
    assert [t for t, _ in alerts] == ["大模型新鲜度厂商异常"]


# ---------- ④：yml 有而基线无的厂商——不提问、进 pending、退出码 1 ----------


def test_vendor_in_yml_missing_from_baseline_fails_without_asking(
        tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)  # yml 两家；基线覆写为只含 anthropic
    save_baseline(
        Baseline(date=BASELINE_DATE, vendors=[_baseline().vendors[0]]),
        baseline_path(Path("data"), BASELINE_DATE))
    fake = FakeBackend({"Anthropic": EMPTY_DAILY})
    _stub_backend(monkeypatch, fake)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 1

    assert len(fake.queries) == 1  # 只问了 anthropic
    assert "月之暗面" not in fake.queries[0]
    pending = Path(PENDING_MD).read_text("utf-8")
    assert "未建基线" in pending and "--vendor moonshot" in pending
    assert "大模型新鲜度厂商异常" in [t for t, _ in alerts]


# ---------- ⑤：结构性错误（鉴权失败）——中止运行、不写基线、退出码 1 ----------


def test_structural_error_aborts_no_baseline_exit_one(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    fake = FakeBackend({"Anthropic": AgentStructuralError("HTTP 401")})
    _stub_backend(monkeypatch, fake)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 1

    assert not baseline_path(Path("data"), DATE).exists()
    assert [t for t, _ in alerts] == ["大模型新鲜度日报失败"]
    assert "HTTP 401" in alerts[0][1]


# ---------- ⑥：完全没有基线——结构性失败，提示 --bootstrap ----------


def test_no_baseline_at_all_is_structural(tmp_path, monkeypatch):
    repo, fake = _setup(tmp_path, monkeypatch, baseline=False)
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 1

    assert fake.queries == []  # 未提问
    assert [t for t, _ in alerts] == ["大模型新鲜度日报失败"]
    assert "--bootstrap" in alerts[0][1]
    assert not baseline_path(Path("data"), DATE).exists()
    assert _git_log_count(repo) == 1


# ---------- ⑦：bootstrap 全量生成草稿——不写报告/README、不提交 ----------


def test_bootstrap_writes_draft_without_commit(tmp_path, monkeypatch):
    repo, fake = _setup(tmp_path, monkeypatch, baseline=False)
    fake.answers = {"Anthropic": BOOTSTRAP_ANTHROPIC, "月之暗面": BOOTSTRAP_MOONSHOT}

    assert main(["vendors", "--date", DATE, "--bootstrap", "--no-commit"]) == 0

    draft = load_baseline(baseline_path(Path("data"), DATE))
    assert draft.schema_version == 4
    states = {v.id: v for v in draft.vendors}
    assert [(m.key, m.release_date, m.added_on, m.retired_on)
            for m in states["anthropic"].models] == [
        ("claude-opus-6", "2026-11-01", DATE, None)]
    assert [m.key for m in states["moonshot"].models] == ["kimi-k3"]
    assert states["anthropic"].last_check.status == "ok"
    assert Path("README.md").read_text("utf-8") == "# free-model-spider\n"
    assert not Path("reports").exists()
    assert _git_log_count(repo) == 1  # 不提交


# ---------- ⑧：已有基线且未指定 --vendor 的 bootstrap 被拒绝 ----------


def test_bootstrap_refuses_when_baseline_exists(tmp_path, monkeypatch):
    _, fake = _setup(tmp_path, monkeypatch)  # 断言打桩给生产代码的那个 fake
    path = baseline_path(Path("data"), BASELINE_DATE)
    before = path.read_bytes()

    assert main(["vendors", "--date", DATE, "--bootstrap", "--no-commit"]) == 1

    assert path.read_bytes() == before  # 原基线未被覆盖
    assert fake.queries == []  # 被拒后不得有任何提问


# ---------- ⑨：--vendor 只替换该厂商块，其余厂商原样保留 ----------


def test_bootstrap_vendor_merges_only_that_vendor(tmp_path, monkeypatch):
    repo, fake = _setup(tmp_path, monkeypatch)
    original = _baseline()
    fake.answers = {"月之暗面": BOOTSTRAP_MOONSHOT}

    assert main(["vendors", "--date", DATE, "--bootstrap", "--vendor",
                 "moonshot", "--no-commit"]) == 0

    assert all("Anthropic" not in q for q in fake.queries)  # 只问 moonshot
    saved = load_baseline(baseline_path(Path("data"), DATE))
    states = {v.id: v for v in saved.vendors}
    assert [m.key for m in states["moonshot"].models] == ["kimi-k3"]
    assert states["moonshot"].last_check is not None
    assert states["anthropic"].model_dump(by_alias=True) == \
        original.vendors[0].model_dump(by_alias=True)
    assert not Path("reports").exists()
    assert Path("README.md").read_text("utf-8") == "# free-model-spider\n"
    assert _git_log_count(repo) == 1


# ---------- ⑩：--vendor 不在 yml——拒绝且不提问 ----------


def test_bootstrap_unknown_vendor_id_rejected(tmp_path, monkeypatch):
    _, fake = _setup(tmp_path, monkeypatch, baseline=False)  # 断言打桩给生产代码的那个 fake

    assert main(["vendors", "--date", DATE, "--bootstrap", "--vendor",
                 "nope", "--no-commit"]) == 1

    assert fake.queries == []  # 拒绝时不得提问
    assert not baseline_path(Path("data"), DATE).exists()


# ---------- ⑪：bootstrap 暂态厂商——空块 + transient 标记 + 退出码 1 ----------


def test_bootstrap_transient_marks_and_returns_one(tmp_path, monkeypatch):
    repo, fake = _setup(tmp_path, monkeypatch, baseline=False)
    fake.answers = {"Anthropic": AgentTransientError("超时"),
                    "月之暗面": BOOTSTRAP_MOONSHOT}

    assert main(["vendors", "--date", DATE, "--bootstrap", "--no-commit"]) == 1

    draft = load_baseline(baseline_path(Path("data"), DATE))
    states = {v.id: v for v in draft.vendors}
    assert states["anthropic"].models == []  # 暂态厂商写入空 models 块
    assert states["anthropic"].last_check.status == "transient"
    assert [m.key for m in states["moonshot"].models] == ["kimi-k3"]
    assert states["moonshot"].last_check.status == "ok"


# ---------- ⑫：yml 已移除厂商的状态块随日复制保留、不提问不渲染 ----------


def test_removed_vendor_block_carried_forward(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    baseline = _baseline()
    baseline.vendors.append(VendorState(
        id="oldco",
        models=[ModelState(key="oldco-1", name="OldCo 1",
                           release_date="2026-01-01",
                           url="https://www.oldco.com/news/a",
                           added_on="2026-01-01")],
    ))
    save_baseline(baseline, baseline_path(Path("data"), BASELINE_DATE))
    fake = FakeBackend({"Anthropic": EMPTY_DAILY, "月之暗面": EMPTY_DAILY})
    _stub_backend(monkeypatch, fake)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 0

    joined = "\n".join(fake.queries)
    assert "oldco" not in joined and "OldCo" not in joined  # 不提问
    md = Path(REPORT_MD).read_text("utf-8")
    assert "OldCo 1" not in md  # 不渲染
    saved = load_baseline(baseline_path(Path("data"), DATE))
    oldco = next(v for v in saved.vendors if v.id == "oldco")
    assert oldco.model_dump(by_alias=True) == baseline.vendors[2].model_dump(
        by_alias=True)  # 原样保留（只增不删）


# ---------- ⑬：无在表模型——窗口回退 180 天并在当日 -pending 记一行（spec §7.1）----------


def test_no_active_models_falls_back_180d_and_records_pending_line(
        tmp_path, monkeypatch, capsys):
    repo, fake = _setup(tmp_path, monkeypatch)
    baseline = _baseline()
    baseline.vendors[1].models = []  # moonshot 无在表模型 → 窗口回退
    save_baseline(baseline, baseline_path(Path("data"), BASELINE_DATE))
    alerts = _capture_alerts(monkeypatch)

    assert main(["vendors", "--date", DATE, "--no-commit"]) == 0

    pending = Path(PENDING_MD).read_text("utf-8")
    assert "月之暗面：无在表模型，提问窗口回退 180 天" in pending
    assert "Anthropic" not in pending  # 有在表模型的厂商不记
    out = capsys.readouterr().out
    assert "（窗口 2026-05-06）" in out  # moonshot：2026-11-02 - 180 天
    assert "（窗口 2026-08-14）" in out  # anthropic 窗口不受影响
    assert [t for t, _ in alerts] == ["大模型新鲜度厂商异常"]  # 信息行也进异常告警
    assert fake.queries != []  # 照常提问，不跳过
