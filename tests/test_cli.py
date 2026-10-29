import json
import subprocess
from pathlib import Path

import httpx
import respx

from free_model_spider.cli import main
from free_model_spider.core.intro import metadata_card
from free_model_spider.sources.opencode import DOCS_URL

PAGE_DAY1 = {
    "data": [
        {"id": "a/x:free", "name": "X", "created": 1786722910,
         "context_length": 1000, "description": "dx",
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "pricing": {"prompt": "0", "completion": "0"}},
    ]
}
PAGE_DAY2 = {
    "data": [
        {"id": "b/y:free", "name": "Y", "created": 1786800000,
         "context_length": 2000, "description": "dy",
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
         "pricing": {"prompt": "0", "completion": "0"}},
    ]
}


def _mock_api(page):
    return respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=page)
    )


def _git(repo: Path, *args: str):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "README.md").write_text("# free-model-spider\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")


@respx.mock
def test_first_then_second_day(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    route = _mock_api(PAGE_DAY1)
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 0
    report1 = Path("reports/openrouter-2026-09-30-免费模型清单.md").read_text("utf-8")
    assert "首日基线" in report1
    assert "## 🆕 新增——无变化" in report1
    assert Path("data/snapshots/openrouter/2026-09-30.json").exists()

    route.side_effect = [httpx.Response(200, json=PAGE_DAY2)]
    assert main(["run", "--sources", "openrouter", "--date", "2026-10-01", "--no-commit"]) == 0
    report2 = Path("reports/openrouter-2026-10-01-免费模型清单.md").read_text("utf-8")
    assert "免费 1 个（2026-09-30：+1 / -1）" in report2
    assert "### b/y:free" in report2
    assert "## 🗑️ 移除 (1)" in report2
    assert "[a/x:free]" in report2
    readme = Path("README.md").read_text("utf-8")
    assert readme.count("| 2026-10-01 | +1 | -1 |") == 1
    assert readme.count("| 2026-09-30 | +0 | -0 |") == 1
    assert "### OpenRouter" in readme


@respx.mock
def test_report_json_alongside_md(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    route = _mock_api(PAGE_DAY1)
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 0
    json1 = json.loads(
        Path("reports/openrouter-2026-09-30-免费模型清单.json").read_text("utf-8")
    )
    assert json1["baseline_date"] is None
    assert json1["added"] == []
    assert [m["id"] for m in json1["unchanged"]] == ["a/x:free"]

    route.side_effect = [httpx.Response(200, json=PAGE_DAY2)]
    assert main(["run", "--sources", "openrouter", "--date", "2026-10-01", "--no-commit"]) == 0
    json2 = json.loads(
        Path("reports/openrouter-2026-10-01-免费模型清单.json").read_text("utf-8")
    )
    assert json2["baseline_date"] == "2026-09-30"
    assert [m["id"] for m in json2["added"]] == ["b/y:free"]
    assert [m["id"] for m in json2["removed"]] == ["a/x:free"]
    assert json2["unchanged"] == []


@respx.mock
def test_first_day_skips_intro_pipeline(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    calls: list[str] = []

    def fake_build_intro(record, search, llm):
        calls.append(record.id)
        return metadata_card(record)

    monkeypatch.setattr("free_model_spider.cli.build_intro", fake_build_intro)

    route = _mock_api(PAGE_DAY1)
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 0
    assert calls == []  # 首日基线：不生成介绍
    readme = Path("README.md").read_text("utf-8")
    assert "| 2026-09-30 | +0 | -0 |" in readme

    route.side_effect = [httpx.Response(200, json=PAGE_DAY2)]
    assert main(["run", "--sources", "openrouter", "--date", "2026-10-01", "--no-commit"]) == 0
    assert calls == ["b/y:free"]  # 次日：仅新增模型生成介绍
    report2 = Path("reports/openrouter-2026-10-01-免费模型清单.md").read_text("utf-8")
    assert "### b/y:free" in report2


@respx.mock
def test_commit_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    _mock_api(PAGE_DAY1)
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30"]) == 0
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], check=True,
        capture_output=True, text=True,
    ).stdout
    assert "[skip ci]" in log.splitlines()[0]


@respx.mock
def test_no_change_rerun_commit_is_success(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    _mock_api(PAGE_DAY1)
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30"]) == 0
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30"]) == 0  # 无变化重跑：nothing to commit 视为成功
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], check=True,
        capture_output=True, text=True,
    ).stdout
    assert len(log.splitlines()) == 2  # init + 唯一一条日报提交


@respx.mock
def test_fetch_failure_returns_nonzero(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避等待
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(500)
    )
    # 单源失败不再向上抛：返回非零（Actions 红灯），且不留半套产物
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 1
    assert not Path("data/snapshots/openrouter/2026-09-30.json").exists()
    assert not Path("reports/openrouter-2026-09-30-免费模型清单.md").exists()


@respx.mock
def test_source_failure_isolated(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避等待
    respx.get(DOCS_URL).mock(return_value=httpx.Response(500))  # opencode 挂
    _mock_api(PAGE_DAY1)  # openrouter 正常
    rc = main(["run", "--sources", "opencode,openrouter", "--date", "2026-09-30", "--no-commit"])
    assert rc == 1  # 有源失败 → 整体非零，红灯可见
    # 成功源照常出全套产物并进索引，失败源无任何产物
    assert Path("reports/openrouter-2026-09-30-免费模型清单.md").exists()
    assert Path("reports/openrouter-2026-09-30-免费模型清单.json").exists()
    assert Path("data/snapshots/openrouter/2026-09-30.json").exists()
    assert not Path("reports/opencode-2026-09-30-免费模型清单.md").exists()
    assert not Path("data/snapshots/opencode/2026-09-30.json").exists()
    readme = Path("README.md").read_text("utf-8")
    assert "### OpenRouter" in readme
    assert "### OpenCode" not in readme


@respx.mock
def test_all_sources_fail_no_readme_pollution(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避等待
    respx.get(DOCS_URL).mock(return_value=httpx.Response(500))
    respx.get("https://openrouter.ai/api/v1/models").mock(return_value=httpx.Response(500))
    assert main(["run", "--sources", "opencode,openrouter", "--date", "2026-09-30", "--no-commit"]) == 1
    # 全源失败：不往 README 写空索引块
    assert Path("README.md").read_text("utf-8") == "# free-model-spider\n"


@respx.mock
def test_run_failure_source_sends_alert(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过重试退避等待
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "free_model_spider.cli.send_alert",
        lambda client, title, content: alerts.append((title, content)),
    )
    respx.get("https://openrouter.ai/api/v1/models").mock(return_value=httpx.Response(500))
    # 失败源非空 → 发一次告警，退出码仍为 1（原有行为不变）
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 1
    assert len(alerts) == 1
    title, content = alerts[0]
    assert title == "免费模型日报存在失败源"
    assert "openrouter" in content


@respx.mock
def test_run_success_sends_no_alert(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "free_model_spider.cli.send_alert",
        lambda client, title, content: alerts.append((title, content)),
    )
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=PAGE_DAY1)
    )
    assert main(["run", "--sources", "openrouter", "--date", "2026-09-30", "--no-commit"]) == 0
    assert alerts == []  # 全部成功：不发告警
