"""Agent 提问/回答契约：query 模板、回答解析、逐条校验与状态迁移（纯函数层）。

spec §7 契约：query 由本模块模板生成（厂商/域名/窗口/在表清单/输出结构与排除规则）；
agent 回答剥 ```json 围栏后按 pydantic 模型校验，结构不对 → None（整厂商记暂态）；
候选逐条校验（日期窗口、官方域名、已知模型），拒收不影响同批其他条目；
apply_accepted 把接收的候选落入 VendorState（只追加，supersedes 命中则淘汰旧模型）。

域名白名单与 JSON 围栏剥离搬自 v3 vendor_judge.py（Task 4 删除 v3 后由本模块承接）。
"""

import json
import re
from datetime import date
from typing import NamedTuple
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from free_model_spider.core.vendor_state import ModelState, VendorState


class NewModelCandidate(BaseModel):
    """回答中的单个候选模型（extra="ignore"：agent 多给字段不致失败）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    release_date: str
    url: str
    supersedes: str | None = None


class DailyAnswer(BaseModel):
    """定期提问的回答契约（spec §7.1）。"""

    model_config = ConfigDict(extra="ignore")

    new_models: list[NewModelCandidate] = Field(default_factory=list)


class BootstrapAnswer(BaseModel):
    """引导提问的回答契约（spec §7.2）。"""

    model_config = ConfigDict(extra="ignore")

    models: list[NewModelCandidate] = Field(default_factory=list)


class Accepted(NamedTuple):
    """通过校验、可入库的候选；supersedes_key 是换算后的在表 key（可为 None）。"""

    name: str
    release_date: str
    url: str
    supersedes_key: str | None


class Rejected(NamedTuple):
    """被拒收的候选与人类可读原因（进 -pending.md 并参与告警）。"""

    name: str
    reason: str


def slugify_name(name: str) -> str:
    """模型名 → key：casefold → 非字母数字序列折叠为 '-' → 去首尾 '-'。"""
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def host_in_whitelist(url: str, official_domains: list[str]) -> bool:
    """url 主机名须等于白名单域或以其子域结尾（防 openai.com.evil.net 伪装）。"""
    host = urlsplit(url).hostname
    if not host:
        return False
    host = host.lower()
    return any(
        host == d.lower() or host.endswith("." + d.lower()) for d in official_domains
    )


def parse_json_object(text: str) -> dict | None:
    """提取首个 {...} 子串并 json.loads，容错 ```json 围栏；失败返回 None。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_daily_answer(text: str) -> DailyAnswer | None:
    """解析定期回答；顶层结构不对（new_models 缺失/非列表）或条目校验不过 → None。

    new_models 必须显式携带：默认值会吞掉键名拼错的回答（如 {"models": [...]}）。
    """
    data = parse_json_object(text)
    if data is None or not isinstance(data.get("new_models"), list):
        return None
    try:
        return DailyAnswer.model_validate(data)
    except ValidationError:
        return None


def parse_bootstrap_answer(text: str) -> BootstrapAnswer | None:
    """解析引导回答；顶层结构不对（models 缺失/非列表）或条目校验不过 → None。"""
    data = parse_json_object(text)
    if data is None or not isinstance(data.get("models"), list):
        return None
    try:
        return BootstrapAnswer.model_validate(data)
    except ValidationError:
        return None


def _minus_years(today: str, years: int) -> str:
    """today 前推 years 年；2月29日在平年回退为 2月28日。"""
    day = date.fromisoformat(today)
    try:
        return day.replace(year=day.year - years).isoformat()
    except ValueError:  # 仅 2月29日：目标年非闰年
        return day.replace(year=day.year - years, day=28).isoformat()


def _date_reject_reason(release_date: str, window_start_day: str, today: str) -> str | None:
    """日期校验：须 date.fromisoformat 成功且窗口起点 <= 日期 <= 今日；返回拒收原因。"""
    try:
        day = date.fromisoformat(release_date)
    except ValueError:
        return f"发布日期非 ISO 格式：{release_date}"
    if not (date.fromisoformat(window_start_day) <= day <= date.fromisoformat(today)):
        return f"发布日期越界（须在 {window_start_day} ~ {today}）：{release_date}"
    return None


def _resolve_supersedes(value: str | None, state: VendorState) -> str | None:
    """supersedes 解析（spec §7.1）：在表 key 精确 → name casefold 精确换算 key → None（不拒收）。"""
    if not value:
        return None
    for model in state.models:
        if model.key == value:
            return model.key
    folded = value.casefold()
    for model in state.models:
        if model.name.casefold() == folded:
            return model.key
    return None


def build_daily_query(
    vendor_name: str,
    aliases: list[str],
    official_domains: list[str],
    today: str,
    window_start_day: str,
    active: list[ModelState],
) -> str:
    """生成定期提问 query（spec §7.1：厂商/域名/今日/窗口/在表清单/输出结构与排除规则）。"""
    known = "\n".join(
        f"- {model.key}（{model.name}，发布于 {model.release_date}）" for model in active
    )
    names = "、".join([vendor_name, *aliases])
    domains = "、".join(official_domains)
    return f"""今天是 {today}。请调查厂商 {names} 是否发布了新的通用大语言模型版本/新系列。

硬性规则：
- 只报通用大语言模型（含推理、多模态文本模型）的新版本；排除图像/视频/语音/embedding 产品。
- 只报 {window_start_day} 及之后发布的模型；排除下方已知清单中的模型；排除仅改名或仅降价的版本。
- 每个条目必须给官方公告 URL，主机名必须是 {domains} 或其子域；证据 URL 非官方域名的一律不要给。
- supersedes 填写被该新模型取代的在表模型的 key（见下方清单）；没有可省略。
- 无新模型时只返回 {{"new_models": []}}。

已知模型清单（不要报）：
{known or "（无）"}

只输出一个 JSON 对象，不加解释，结构示例：
{{"new_models": [{{"name": "模型名", "release_date": "YYYY-MM-DD", "url": "https://…", "supersedes": "被取代模型的key"}}]}}
"""


def build_bootstrap_query(
    vendor_name: str,
    aliases: list[str],
    official_domains: list[str],
    today: str,
) -> str:
    """生成引导提问 query（spec §7.2：当前在售最新一代清单，窗口 today-3 年）。"""
    names = "、".join([vendor_name, *aliases])
    domains = "、".join(official_domains)
    return f"""今天是 {today}。请列出厂商 {names} 当前在售的**最新一代**通用大语言模型清单（旗舰及其主要规格变体；已被更新一代取代的旧模型不要列）。

硬性规则：
- 只列通用大语言模型（含推理、多模态文本模型）；排除图像/视频/语音/embedding 产品。
- release_date 为该模型发布日期（YYYY-MM-DD），不早于 {today} 往前 3 年。
- 每项给出官方公告 URL，主机名必须是 {domains} 或其子域。
- 不确定的模型不要列，宁缺毋滥。

只输出一个 JSON 对象，不加解释，结构示例：
{{"models": [{{"name": "模型名", "release_date": "YYYY-MM-DD", "url": "https://…"}}]}}
"""


def validate_daily(
    answer: DailyAnswer,
    state: VendorState,
    official_domains: list[str],
    window_start_day: str,
    today: str,
) -> tuple[list[Accepted], list[Rejected], list[str]]:
    """逐条校验定期回答（spec §7.1），返回 (接收, 拒收, 忽略的已知模型名)。

    校验顺序：日期 → 官方域名 → 已知模型（在表 key ∪ 本批已接收的 key，同批
    重复 slug 只入库一次）；supersedes 解析不中不拒收，置 None。
    """
    accepted: list[Accepted] = []
    rejected: list[Rejected] = []
    ignored: list[str] = []
    known = {model.key for model in state.models}
    for candidate in answer.new_models:
        reason = _date_reject_reason(candidate.release_date, window_start_day, today)
        if reason:
            rejected.append(Rejected(candidate.name, reason))
            continue
        if not host_in_whitelist(candidate.url, official_domains):
            rejected.append(Rejected(candidate.name, f"证据域名非官方：{candidate.url}"))
            continue
        slug = slugify_name(candidate.name)
        if slug in known:
            ignored.append(candidate.name)
            continue
        accepted.append(
            Accepted(
                name=candidate.name,
                release_date=candidate.release_date,
                url=candidate.url,
                supersedes_key=_resolve_supersedes(candidate.supersedes, state),
            )
        )
        known.add(slug)  # 本批已接收的也算已知（同批重复 slug 忽略）
    return accepted, rejected, ignored


def validate_bootstrap(
    answer: BootstrapAnswer,
    official_domains: list[str],
    today: str,
) -> tuple[list[Accepted], list[Rejected]]:
    """逐条校验引导回答（spec §7.2）：校验同 §7.1 的日期与域名，窗口 = today-3 年 ~ today。

    无在表状态可比对，不做已知模型忽略；supersedes 忽略（supersedes_key 恒 None）。
    """
    window_start_day = _minus_years(today, 3)
    accepted: list[Accepted] = []
    rejected: list[Rejected] = []
    for candidate in answer.models:
        reason = _date_reject_reason(candidate.release_date, window_start_day, today)
        if reason:
            rejected.append(Rejected(candidate.name, reason))
            continue
        if not host_in_whitelist(candidate.url, official_domains):
            rejected.append(Rejected(candidate.name, f"证据域名非官方：{candidate.url}"))
            continue
        accepted.append(
            Accepted(
                name=candidate.name,
                release_date=candidate.release_date,
                url=candidate.url,
                supersedes_key=None,
            )
        )
    return accepted, rejected


def apply_accepted(
    state: VendorState,
    accepted: list[Accepted],
    today: str,
) -> list[ModelState]:
    """接收条目落入状态（spec §8.1）：追加新 ModelState（added_on=today）；supersedes_key
    命中在表未退役模型 → 旧模型 retired_on=today、superseded_by=新 key。返回新增列表。
    """
    added: list[ModelState] = []
    for item in accepted:
        key = slugify_name(item.name)
        state.models.append(
            ModelState(
                key=key,
                name=item.name,
                release_date=item.release_date,
                url=item.url,
                added_on=today,
            )
        )
        added.append(state.models[-1])
        if item.supersedes_key:
            for model in state.models:
                if model.key == item.supersedes_key and model.retired_on is None:
                    model.retired_on = today
                    model.superseded_by = key
    return added
