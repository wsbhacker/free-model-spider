"""提问/回答契约测试：slug 化、域名白名单、JSON 解析、逐条校验与状态迁移。"""

from free_model_spider.core.vendor_agent import (
    Accepted,
    BootstrapAnswer,
    DailyAnswer,
    NewModelCandidate,
    apply_accepted,
    build_bootstrap_query,
    build_daily_query,
    host_in_whitelist,
    parse_bootstrap_answer,
    parse_daily_answer,
    slugify_name,
    validate_bootstrap,
    validate_daily,
)
from free_model_spider.core.vendor_state import ModelState, VendorState

DOMAINS = ["anthropic.com"]
TODAY = "2026-11-02"
WINDOW = "2026-08-14"


def _state() -> VendorState:
    return VendorState(
        id="anthropic",
        models=[
            ModelState(
                key="claude-opus-5-5",
                name="Claude Opus 5.5",
                release_date="2026-08-14",
                url="https://www.anthropic.com/news/a",
                added_on="2026-08-14",
            ),
        ],
    )


def _candidate(name: str, release_date: str, url: str, **kwargs: str) -> NewModelCandidate:
    return NewModelCandidate(name=name, release_date=release_date, url=url, **kwargs)


def test_slugify_name():
    assert slugify_name("Claude Opus 5.5") == "claude-opus-5-5"
    assert slugify_name("GLM-5.3-Flash!") == "glm-5-3-flash"
    assert slugify_name("  Qwen3.8 Max ") == "qwen3-8-max"


def test_host_in_whitelist_blocks_lookalike():
    assert not host_in_whitelist("https://openai.com.evil.net/x", ["openai.com"])
    assert host_in_whitelist("https://blog.google/ai/release", ["blog.google"])
    assert host_in_whitelist("https://www.anthropic.com/news/a", DOMAINS)  # 子域
    assert not host_in_whitelist("not-a-url", DOMAINS)


def test_parse_daily_answer_fences_and_plain():
    fenced = (
        "```json\n"
        '{"new_models": [{"name": "Claude Opus 6", "release_date": "2026-11-01", '
        '"url": "https://www.anthropic.com/news/opus6", "evidence": "多余字段"}]}\n'
        "```"
    )
    answer = parse_daily_answer(fenced)
    assert answer is not None
    assert len(answer.new_models) == 1  # 条目多余字段被 ignore
    assert answer.new_models[0].name == "Claude Opus 6"

    plain = '{"new_models": [], "conversation_id": "abc"}'  # 顶层多余字段被 ignore
    assert parse_daily_answer(plain) == DailyAnswer(new_models=[])


def test_daily_answer_schema_violation_returns_none():
    assert parse_daily_answer('{"models": []}') is None  # 键名不对（缺 new_models）
    assert parse_daily_answer('{"new_models": "x"}') is None  # 不是列表
    assert parse_daily_answer(
        '{"new_models": [{"name": "X", "release_date": "2026-11-01"}]}'
    ) is None  # 条目缺 url
    assert parse_daily_answer("我不确定，没有新模型。") is None  # 纯文本


def test_parse_bootstrap_answer_symmetric():
    fenced = "```json\n" '{"models": [{"name": "Claude Opus 6", "release_date": "2026-11-01", "url": "https://www.anthropic.com/news/opus6"}]}\n' "```"
    answer = parse_bootstrap_answer(fenced)
    assert answer is not None and len(answer.models) == 1
    assert parse_bootstrap_answer('{"new_models": []}') is None  # 键名不对（缺 models）


def test_validate_daily_accepts_and_resolves_supersedes_by_name():
    answer = DailyAnswer(
        new_models=[
            _candidate(
                "Claude Opus 6",
                "2026-11-01",
                "https://www.anthropic.com/news/opus6",
                supersedes="Claude Opus 5.5",
            ),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert rejected == [] and ignored == []
    assert len(accepted) == 1
    assert accepted[0].supersedes_key == "claude-opus-5-5"  # 按 name casefold 换算为 key


def test_validate_daily_rejects_non_iso_dates():
    names = ["A", "B", "C"]
    dates = ["2026/11/01", "November 1, 2026", "2026-11-01T10:00"]
    answer = DailyAnswer(
        new_models=[
            _candidate(name, day, "https://www.anthropic.com/news/x")
            for name, day in zip(names, dates, strict=True)
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert accepted == [] and ignored == []
    assert [r.name for r in rejected] == names  # 逐条拒收，不影响同批
    for r in rejected:
        assert "日期" in r.reason


def test_validate_daily_rejects_out_of_window():
    answer = DailyAnswer(
        new_models=[
            _candidate("Too Old", "2026-08-13", "https://www.anthropic.com/news/old"),
            _candidate("Future", "2026-11-03", "https://www.anthropic.com/news/future"),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert accepted == [] and ignored == []
    assert [r.name for r in rejected] == ["Too Old", "Future"]
    for r in rejected:
        assert "日期" in r.reason


def test_validate_daily_rejects_non_official_url():
    answer = DailyAnswer(
        new_models=[
            _candidate("Claude Opus 6", "2026-11-01", "https://openai.com.evil.net/x"),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert accepted == [] and ignored == []
    assert rejected[0].name == "Claude Opus 6"  # 拒收项 name 原样
    assert "域名" in rejected[0].reason


def test_validate_daily_ignores_known_model():
    answer = DailyAnswer(
        new_models=[
            _candidate("Claude Opus 5.5", "2026-08-14", "https://www.anthropic.com/news/a"),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert accepted == [] and rejected == []
    assert ignored == ["Claude Opus 5.5"]


def test_duplicate_slug_within_answer_ignored():
    answer = DailyAnswer(
        new_models=[
            _candidate("GLM-5.3", "2026-11-01", "https://www.anthropic.com/news/glm"),
            _candidate("GLM 5.3", "2026-11-01", "https://www.anthropic.com/news/glm2"),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert rejected == []
    assert [a.name for a in accepted] == ["GLM-5.3"]  # 只入库一次
    assert ignored == ["GLM 5.3"]  # 同 slug 的第二个按已知忽略


def test_supersedes_unknown_becomes_none():
    answer = DailyAnswer(
        new_models=[
            _candidate(
                "Claude Opus 6",
                "2026-11-01",
                "https://www.anthropic.com/news/opus6",
                supersedes="gpt-7",
            ),
        ],
    )
    accepted, rejected, ignored = validate_daily(answer, _state(), DOMAINS, WINDOW, TODAY)
    assert rejected == [] and ignored == []  # 不拒收
    assert accepted[0].supersedes_key is None


def test_apply_accepted_adds_and_retires():
    state = _state()
    accepted = [
        Accepted(
            name="Claude Opus 6",
            release_date="2026-11-01",
            url="https://www.anthropic.com/news/opus6",
            supersedes_key="claude-opus-5-5",
        ),
    ]
    added = apply_accepted(state, accepted, TODAY)
    assert len(state.models) == 2  # 只追加不删除
    assert added[0].key == "claude-opus-6" and added[0].added_on == "2026-11-02"
    old = state.models[0]
    assert old.retired_on == "2026-11-02" and old.superseded_by == "claude-opus-6"
    assert "claude-opus-5-5" not in [m.key for m in state.active_models()]


def test_build_daily_query_contains_contract():
    query = build_daily_query(
        "Anthropic", ["Claude"], DOMAINS, TODAY, WINDOW, _state().active_models(),
    )
    assert "Anthropic" in query
    assert "Claude" in query  # 别名
    assert "anthropic.com" in query
    assert "2026-11-02" in query  # 今日日期
    assert "2026-08-14" in query  # 窗口起点
    assert "claude-opus-5-5（Claude Opus 5.5，发布于 2026-08-14）" in query  # 在表清单条目
    assert "new_models" in query
    assert '{"new_models": []}' in query  # 无新模型的输出


def test_build_bootstrap_query_contains_contract():
    query = build_bootstrap_query("Anthropic", ["Claude"], DOMAINS, TODAY)
    assert "models" in query
    assert "最新一代" in query
    assert "embedding" in query


def test_validate_bootstrap_window_three_years():
    answer = BootstrapAnswer(
        models=[
            _candidate(
                "Just In Window",
                "2023-11-03",
                "https://www.anthropic.com/news/in",
                supersedes="claude-opus-5-5",
            ),
            _candidate("Too Old", "2023-11-01", "https://www.anthropic.com/news/old"),
        ],
    )
    accepted, rejected = validate_bootstrap(answer, DOMAINS, TODAY)
    assert [a.name for a in accepted] == ["Just In Window"]
    assert accepted[0].supersedes_key is None  # bootstrap 忽略 supersedes
    assert [r.name for r in rejected] == ["Too Old"]
