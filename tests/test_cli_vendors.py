"""fms vendors v3 编排测试：人工圈定清单 + 通道优先级 + 发现告警 + 永固幂等。

stub LLM + respx 公告列表页/文章页/目录 API；zai（有公告源 headings 模式）+
openai（无公告源，目录日期通道）复刻真实形态（v3 spec §4/§5）。
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from free_model_spider.cli import main
from free_model_spider.core.vendor_report import FMS_VENDORS_START
from free_model_spider.core.vendor_stats import (
    Baseline,
    ModelState,
    VendorState,
    baseline_path,
    load_baseline,
    save_baseline,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
UPDATES_URL = "https://docs.z.ai/release-notes/new-released"

CONFIG_YAML = f"""\
vendors:
  - id: zai
    name: 智谱 AI
    official_domains: [z.ai]
    directory_slugs: {{ openrouter: [z-ai], modelsdev: [zai] }}
    announcements:
      - {{ name: 发布说明, url: "{UPDATES_URL}", mode: headings }}
    models:
      - key: glm-5-3
        name: GLM-5.3
        match: ['glm[ -]?5\\.3']
        exclude: [flash, flashx, prime, air]
      - key: glm-5-3-flash
        name: GLM-5.3-Flash
        match: ['glm[ -]?5\\.3[ -]?flash']
  - id: openai
    name: OpenAI
    official_domains: [openai.com]
    directory_slugs: {{ openrouter: [openai] }}
    # 无公告源：目录日期通道（v3 spec §4 通道 2/3）
    models:
      - key: gpt-6-1-terra
        name: GPT-6.1 Terra
        match: ['gpt[ -]?6\\.1[ -]?terra']
        exclude: [pro]
"""

MANUAL_CONFIG_YAML = CONFIG_YAML.replace(
    """      - key: glm-5-3-flash
        name: GLM-5.3-Flash
        match: ['glm[ -]?5\\.3[ -]?flash']""",
    """      - key: glm-5-3-flash
        name: GLM-5.3-Flash
        match: ['glm[ -]?5\\.3[ -]?flash']
        manual: { date: "2026-08-26", url: "https://z.ai/glm-5-3-flash" }""",
)

UPDATES_HTML = """<html><body>
<h1>Release Notes</h1>
<h2>Date: 2026-08-18</h2>
<h3>GLM-5.3 Release</h3><p>flagship</p>
<h2>Date: 2026-08-26</h2>
<h3>GLM-5.3-Flash Release</h3><p>flash</p>
</body></html>"""

GLM_JSON = (
    '{"model": "GLM-5.3", "release_date": "2026-08-18", '
    '"evidence": ["GLM-5.3 于 2026-08-18 正式发布"], "reason": ""}'
)
FLASH_JSON = (
    '{"model": "GLM-5.3-Flash", "release_date": "2026-08-26", '
    '"evidence": ["GLM-5.3-Flash 于 2026-08-26 正式发布"], "reason": ""}'
)
INSUFFICIENT_JSON = (
    '{"model": null, "release_date": null, "evidence": [], '
    '"reason": "页面原文未出现发布日期"}'
)

REPORT_MD = "reports/各大厂商大模型新鲜度-2026-10-29.md"
PENDING_MD = "reports/各大厂商大模型新鲜度-2026-10-29-pending.md"


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp())


def _dir_payload(with_glm6: bool = False) -> dict:
    ids = [
        ("z-ai/glm-5.3", _ts("2026-08-18")),
        ("z-ai/glm-5.3-flash", _ts("2026-08-26")),
        ("z-ai/glm-4", _ts("2025-01-01")),  # 未维护条目：首跑播种不告警
        ("openai/gpt-6.1-terra", _ts("2026-10-26")),
        ("openai/gpt-6.1-terra-pro", _ts("2026-10-26")),  # terra 的 exclude=pro 挡
    ]
    if with_glm6:
        ids.append(("z-ai/glm-6", _ts("2026-10-28")))  # 未维护新模型：发现告警
    return {"data": [{"id": i, "created": c} for i, c in ids]}


MODELS_DEV_PAYLOAD = {"zai": {"models": {"glm-5.3": {"release_date": "2026-08-18"}}}}


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


def _capture_alerts(monkeypatch) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "free_model_spider.cli.send_alert",
        lambda client, title, content: alerts.append((title, content)),
    )
    return alerts


def _mock_offline(monkeypatch, with_glm6: bool = False):
    """目录两源 + 公告列表页（headings 模式条目与文章页同 URL）的 respx 打桩。"""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    respx.get(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=_dir_payload(with_glm6)))
    respx.get(MODELS_DEV_URL).mock(
        return_value=httpx.Response(200, json=MODELS_DEV_PAYLOAD))
    respx.get(UPDATES_URL).mock(
        return_value=httpx.Response(200, text=UPDATES_HTML))


def _confirmed_baseline() -> Baseline:
    """全部已确认的既有基线（次日幂等/发现告警场景的起点）。"""
    return Baseline(
        date="2026-10-28",
        vendors=[
            VendorState(
                id="zai",
                seen_directory_ids=[
                    f.casefold() for f in
                    ("z-ai/glm-5.3", "z-ai/glm-5.3-flash", "z-ai/glm-4",
                     "openai/gpt-6.1-terra", "openai/gpt-6.1-terra-pro",
                     "zai:glm-5.3")],
                models=[
                    ModelState(
                        key="glm-5-3", name="GLM-5.3", date="2026-08-18",
                        url=UPDATES_URL, source="official", status="confirmed",
                        last_verified="2026-10-28",
                    ),
                    ModelState(
                        key="glm-5-3-flash", name="GLM-5.3-Flash", date="2026-08-26",
                        url=UPDATES_URL, source="official", status="confirmed",
                        last_verified="2026-10-28",
                    ),
                ],
            ),
            VendorState(
                id="openai",
                seen_directory_ids=[
                    f.casefold() for f in
                    ("openai/gpt-6.1-terra", "openai/gpt-6.1-terra-pro")],
                models=[ModelState(
                    key="gpt-6-1-terra", name="GPT-6.1 Terra", date="2026-10-26",
                    url=None, source="directory", status="confirmed",
                    reason="目录日期（openrouter）", last_verified="2026-10-28",
                )],
            ),
        ],
    )


# ---------- 场景①：首跑——公告源逐模型提取 + 无公告源厂商目录确认 + 静默播种 ----------


@respx.mock
def test_first_run_confirms_via_both_channels(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    alerts = _capture_alerts(monkeypatch)
    llm = FakeLLM([GLM_JSON, FLASH_JSON])  # zai 两模型各一次提取
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert len(llm.calls) == 2  # openai 走目录通道 0 LLM；zai 每模型恰 1 次
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai, openai = saved.vendors
    assert [(m.date, m.source, m.status) for m in zai.models] == [
        ("2026-08-18", "official", "confirmed"),
        ("2026-08-26", "official", "confirmed"),
    ]
    assert (openai.models[0].date, openai.models[0].source,
            openai.models[0].status) == ("2026-10-26", "directory", "confirmed")
    # 首跑播种：全部目录条目入 seen（含未维护 glm-4），不发发现告警
    assert "z-ai/glm-4" in zai.seen_directory_ids
    assert alerts == []
    assert "首跑播种" in capsys.readouterr().out
    md = Path(REPORT_MD).read_text("utf-8")
    assert "[GLM-5.3](https://docs.z.ai/release-notes/new-released) [⧉](https://openrouter.ai/z-ai/glm-5.3)" in md
    assert "[GPT-6.1 Terra](https://openrouter.ai/openai/gpt-6.1-terra)" in md
    assert "2026-10-26†" in md  # 目录来源日期带 †
    assert not Path(PENDING_MD).exists()  # 全确认 → 无未决明细


# ---------- 场景②：confirmed 永固——次日重跑零 LLM、日期不动 ----------


@respx.mock
def test_confirmed_models_immutable_on_rerun(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _capture_alerts(monkeypatch)
    save_baseline(_confirmed_baseline(), baseline_path(Path("data"), "2026-10-28"))
    llm = FakeLLM([])  # 全 confirmed：不得有任何 LLM 调用
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert llm.calls == []
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = saved.vendors[0]
    assert [(m.date, m.last_verified) for m in zai.models] == [
        ("2026-08-18", "2026-10-29"),  # 事实永固，仅核实日重臂
        ("2026-08-26", "2026-10-29"),
    ]


# ---------- 场景③：提取失败暂态——不降级目录、进异常告警、-pending 明细 ----------


@respx.mock
def test_extraction_failure_transient_not_downgraded(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    alerts = _capture_alerts(monkeypatch)
    save_baseline(_confirmed_baseline(), baseline_path(Path("data"), "2026-10-28"))
    # 撤掉 zai 两个模型的确认，强迫重提取；openai 保持 confirmed
    baseline = load_baseline(baseline_path(Path("data"), "2026-10-28"))
    baseline.vendors[0].models = [
        ModelState(key="glm-5-3", name="GLM-5.3"),
        ModelState(key="glm-5-3-flash", name="GLM-5.3-Flash"),
    ]
    save_baseline(baseline, baseline_path(Path("data"), "2026-10-28"))
    llm = FakeLLM([INSUFFICIENT_JSON, INSUFFICIENT_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = saved.vendors[0]
    # 暂态铁律：z-ai/glm-5.3 目录里明明有日期也不得降级
    assert all(m.status == "unconfirmed" for m in zai.models)
    assert all("暂态" in m.reason for m in zai.models)
    # 异常摘要告警恰一次，两个模型都进正文
    assert [t for t, _ in alerts] == ["大模型新鲜度厂商异常"]
    content = alerts[0][1]
    assert "zai/glm-5-3 " in content and "zai/glm-5-3-flash " in content
    ptext = Path(PENDING_MD).read_text("utf-8")
    assert "GLM-5.3" in ptext and "暂态" in ptext
    # 主表只剩 openai（目录确认行）
    md = Path(REPORT_MD).read_text("utf-8")
    assert "GPT-6.1 Terra" in md and "GLM-5.3" not in md


# ---------- 场景④：发现告警——未维护新目录条目 webhook + 入 seen ----------


@respx.mock
def test_unmaintained_new_directory_entry_alerts(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    alerts = _capture_alerts(monkeypatch)
    save_baseline(_confirmed_baseline(), baseline_path(Path("data"), "2026-10-28"))
    _stub_no_llm(monkeypatch)  # 全 confirmed：无 LLM 也能跑完
    _mock_offline(monkeypatch, with_glm6=True)  # 目录多出未维护的 glm-6

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert [t for t, _ in alerts] == ["发现未维护新模型"]
    assert "z-ai/glm-6" in alerts[0][1]
    assert "vendor_releases.yml" in alerts[0][1]  # 告警给出人工动作指引
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    zai = saved.vendors[0]
    assert "z-ai/glm-6" in zai.seen_directory_ids  # 告警后入 seen 不重复
    # 同日重跑：已入 seen → 不再告警
    alerts.clear()
    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0
    assert alerts == []


# ---------- 场景⑤：manual 纠偏——配置即 confirmed、不参与提取 ----------


@respx.mock
def test_manual_ref_confirms_directly(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "data" / "vendor_releases.yml").write_text(MANUAL_CONFIG_YAML,
                                                       encoding="utf-8")
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _capture_alerts(monkeypatch)
    llm = FakeLLM([GLM_JSON])  # 只有 glm-5-3 走提取
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29", "--no-commit"]) == 0

    assert len(llm.calls) == 1
    saved = load_baseline(baseline_path(Path("data"), "2026-10-29"))
    flash = saved.vendors[0].models[1]
    assert (flash.date, flash.url, flash.source, flash.status) == (
        "2026-08-26", "https://z.ai/glm-5-3-flash", "manual", "confirmed")


# ---------- 场景⑥：提交链路（data/vendor-models + reports + readme，[skip ci]） ----------


@respx.mock
def test_commit_message_and_tracked_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    _clean_env(monkeypatch)
    _capture_alerts(monkeypatch)
    llm = FakeLLM([GLM_JSON, FLASH_JSON])
    _stub_llm(monkeypatch, llm)
    _mock_offline(monkeypatch)

    assert main(["vendors", "--date", "2026-10-29"]) == 0  # 默认提交
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         check=True, capture_output=True, text=True).stdout
    assert log.splitlines()[0].endswith("docs: 大模型新鲜度日报 2026-10-29 [skip ci]")
    tracked = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files"],
        check=True, capture_output=True, text=True).stdout
    assert "data/vendor-models/2026-10-29.json" in tracked  # v3 新基线目录
    assert "reports/各大厂商大模型新鲜度-2026-10-29.md" in tracked
    assert "data/vendor-releases/" not in tracked  # v2 旧目录不再写入
    readme = Path("README.md").read_text("utf-8")
    assert FMS_VENDORS_START in readme
