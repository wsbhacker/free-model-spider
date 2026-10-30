"""官网公告源的通用抓取与解析（v2 官网爬取主通道，redesign spec §3/§4）。

不做每厂商一个的 selector，而是按源配置解析模式（mode），共四种：

- ``sitemap``：站点 sitemap.xml 的 (loc, lastmod)，title 由 URL slug 还原；
  日期为发现时间（lastmod），真值由文章页提取阶段给出。
- ``headings``：内容型 changelog——日期做节标题（google "October 27, 2026"、
  deepseek "Date: 2026-10-17"），模型名是其子节标题；节日期 + 子节标题配对成条目。
- ``links``：导航列表页——<a> 锚文本为标题，锚点内/后/前窗口找日期。
- ``json``：页面 script 内嵌 JSON 的 (title, 日期键) 对（anthropic publishedOn）。

上游改版时零候选 → 上层打印降级告警，沿用昨日基线出数。
"""

import re
from dataclasses import dataclass
from datetime import date as _date
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

from free_model_spider.core.vendor_config import VendorConfig
from free_model_spider.net import request_with_retries

ANNOUNCEMENT_MAX_ENTRIES = 60  # 单源最多收录条数（防失控页面撑爆基线 JSON）
_TITLE_MIN_CHARS = 4
_DATE_WINDOW = 300  # links 模式锚点前/后各搜日期的字符窗口
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.S | re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*?href\s*=\s*[\"']?([^\"'\s>]+)[\"']?[^>]*>(.*?)</a>", re.S | re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# headings 模式：id 直接是日期的节元素（zhipu <div class="update" id="2026-08-26">）
_ID_DATE_RE = re.compile(r"<(\w+)\b[^>]*\bid=\"(20\d{2}-\d{2}-\d{2})\"[^>]*>")
_STRONG_RE = re.compile(r"<strong\b[^>]*>(.*?)</strong>", re.S | re.I)

_ISO_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_CN_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_EN_MD_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(20\d{2})\b",
    re.I,
)
_EN_DM_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")[a-z]*\.?,?\s+(20\d{2})\b",
    re.I,
)
# json 模式：script 内 title 键与邻近日期键的配对（允许转义引号，anthropic Next 数据实测形态）
_JSON_PAIR_RE = re.compile(
    r'\\?"(?:title|name)\\?":\s*\\?"([^"\\]{6,120})\\?"[^{}\\]{0,400}?'
    r'\\?"(?:publishedOn|published_at|publishedAt|publishTime|date)\\?":\s*\\?"(20\d{2}-\d{2}-\d{2})\\?"'
)


@dataclass(frozen=True)
class AnnouncementEntry:
    """公告源解析出的单条候选：标题、站内 URL、ISO 日期（解析不到为 None）。"""

    title: str
    url: str
    date: str | None


def _normalize_date(year: int, month: int, day: int) -> str | None:
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def _find_date(text: str) -> str | None:
    """文本内按 ISO → 中文 → 英文月份的顺序找首个合法日期，归一化为 ISO。"""
    m = _ISO_RE.search(text)
    if m:
        normalized = _normalize_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if normalized:
            return normalized
    m = _CN_RE.search(text)
    if m:
        normalized = _normalize_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if normalized:
            return normalized
    m = _EN_MD_RE.search(text)
    if m:
        normalized = _normalize_date(
            int(m.group(3)), _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))
        )
        if normalized:
            return normalized
    m = _EN_DM_RE.search(text)
    if m:
        normalized = _normalize_date(
            int(m.group(3)), _MONTHS.index(m.group(2).lower()[:3]) + 1, int(m.group(1))
        )
        if normalized:
            return normalized
    return None


def _host_allowed(url: str, official_domains: list[str]) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    host = host.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in official_domains)


def _clean_text(html: str) -> str:
    return unescape(_WS_RE.sub(" ", _TAG_RE.sub(" ", html))).strip()


def _cap(entries: list[AnnouncementEntry]) -> list[AnnouncementEntry]:
    return entries[:ANNOUNCEMENT_MAX_ENTRIES]


# --------------------------------------------------------------------------
# sitemap 模式
# --------------------------------------------------------------------------


def _slug_title(url: str) -> str:
    """URL 末段还原标题。保留连字符（门筛正则按连字符书写，如 minimax-m\\d）。"""
    segment = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return unescape(segment).strip()


def parse_sitemap(
    xml: str, page_url: str, official_domains: list[str], include: str | None
) -> list[AnnouncementEntry]:
    """sitemap.xml → (slug 标题, loc, lastmod) 条目；include 正则过滤路径（如 /news/）。
    sitemap 文档序与时间无关（常为字母序），先按日期降序再截断，保住最新条目。"""
    entries: list[AnnouncementEntry] = []
    seen_urls: set[str] = set()
    pattern = re.compile(include) if include else None
    for block in re.findall(r"<item\b.*?</item>|<url\b.*?</url>", xml, re.S | re.I):
        loc_m = re.search(r"<loc>\s*([^<]+?)\s*</loc>", block, re.I)
        if not loc_m:
            continue
        url = urljoin(page_url, loc_m.group(1))
        if not _host_allowed(url, official_domains):
            continue
        if pattern and not pattern.search(urlsplit(url).path):
            continue
        mod_m = re.search(r"<lastmod>\s*([^<]+?)\s*</lastmod>", block, re.I)
        date = _find_date(mod_m.group(1)) if mod_m else None
        title = _slug_title(url)
        if len(title) < _TITLE_MIN_CHARS or url in seen_urls:
            continue
        seen_urls.add(url)
        entries.append(AnnouncementEntry(title, url, date))
    entries.sort(key=lambda entry: (entry.date or ""), reverse=True)
    return _cap(entries)


# --------------------------------------------------------------------------
# headings 模式（内容型 changelog：日期节标题 + 模型名子节标题）
# --------------------------------------------------------------------------


def _date_only_title(title: str) -> bool:
    """标题去掉日期后是否只剩噪声——纯日期标题无法门筛也无法归类，直接跳过。"""
    residue = _ISO_RE.sub(" ", title)
    residue = _CN_RE.sub(" ", residue)
    residue = _EN_MD_RE.sub(" ", residue)
    residue = _EN_DM_RE.sub(" ", residue)
    return len(_WS_RE.sub("", re.sub(r"[^\w\s]", " ", residue))) < 3


def parse_headings(
    html: str, page_url: str, official_domains: list[str]
) -> list[AnnouncementEntry]:
    """内容型 changelog 解析（google/deepseek 实测结构，redesign spec §3）。

    递归下降 heading 树：节日期只认 heading 自身标签/文本（google h2
    "October 27, 2026"、deepseek h2 "Date: 2026-10-17"）或继承父节；叶节产出条目，
    纯日期标题退化到节内 <strong> 短语（google 模型条目）或站内锚文本。
    另支持 id 即日期的节容器（zhipu ``<div id="2026-08-26">``，锚点 slug 作标题）。
    条目 URL 回填列表页自身，文章页提取阶段抓该页解析。
    """
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    headings = [
        (int(m.group(1)), _clean_text(m.group(2)), m.group(0), m.start(), m.end())
        for m in _HEADING_RE.finditer(cleaned)
    ]
    id_matches = list(_ID_DATE_RE.finditer(cleaned))
    id_spans = [
        (m.start(), id_matches[i + 1].start() if i + 1 < len(id_matches) else len(cleaned))
        for i, m in enumerate(id_matches)
    ]

    def inside_id_section(pos: int) -> bool:
        return any(s <= pos < e for s, e in id_spans)

    def leaf_titles(text: str, section: str) -> list[str]:
        """叶节标题：heading 文本非纯日期直接用；否则 <strong>，再否则站内锚标题。
        锚标题 = 锚文本 + URL slug 拼接（zhipu 锚文本是 "documentation"，模型名在
        slug 里；门筛与 LLM 归类都扫描整串，两段都保留）。纯日期候选一律丢弃。"""
        if not _date_only_title(text):
            return [text]
        made = [
            t for t in (_clean_text(s) for s in _STRONG_RE.findall(section))
            if len(t) >= _TITLE_MIN_CHARS and not _date_only_title(t)
        ]
        if not made:
            for a in _ANCHOR_RE.finditer(section):
                href = a.group(1)
                if href.startswith("#") or not _host_allowed(
                    urljoin(page_url, href), official_domains
                ):
                    continue
                anchor_text = _clean_text(a.group(2))
                slug = _slug_title(urljoin(page_url, href))
                title = f"{anchor_text} {slug}".strip() if anchor_text else slug
                if len(title) >= _TITLE_MIN_CHARS and not _date_only_title(title):
                    made.append(title)
        return made

    entries: list[AnnouncementEntry] = []
    seen: set[tuple[str, str]] = set()

    def emit(title: str, date: str) -> None:
        key = (title.casefold(), date)
        if key not in seen and len(title) >= _TITLE_MIN_CHARS:
            seen.add(key)
            entries.append(AnnouncementEntry(title, page_url, date))

    def walk(idx: int, inherited: str | None) -> None:
        level, text, raw, start, end = headings[idx]
        if inside_id_section(start):
            return  # id 节容器自有解析，heading 不重复产出
        section_end = len(cleaned)
        for j in range(idx + 1, len(headings)):
            if headings[j][0] <= level:
                section_end = headings[j][3]
                break
        date = _find_date(f"{raw} {text}") or inherited
        children = [
            j for j in range(idx + 1, len(headings))
            if headings[j][3] < section_end
        ]
        if children:
            min_level = min(headings[j][0] for j in children)
            for j in children:
                if headings[j][0] == min_level:
                    walk(j, date)
        elif date is not None and not inside_id_section(end):
            for title in leaf_titles(text, cleaned[end:section_end]):
                emit(title.strip(), date)

    for idx in range(len(headings)):
        walk(idx, None)

    for i, m in enumerate(id_matches):
        date = m.group(2)
        section = cleaned[m.end() : id_spans[i][1]]
        for title in leaf_titles(date, section):  # 纯日期标题 → 走 strong/锚点候选
            emit(title, date)
    return _cap(entries)


def _find_date_scoped(m: re.Match, cleaned: str) -> str | None:
    """links 模式日期定位：按 锚点内文本 → 锚点后窗 → 锚点前窗 的顺序找日期
    （列表页日期多在链接内部或其后；前窗放最后以减少左邻居日期渗漏）。"""
    inner = m.group(2)
    after = cleaned[m.end() : m.end() + _DATE_WINDOW]
    before = cleaned[max(0, m.start() - _DATE_WINDOW) : m.start()]
    for scope in (inner, after, before):
        found = _find_date(scope)
        if found:
            return found
    return None


def parse_links(
    html: str, page_url: str, official_domains: list[str]
) -> list[AnnouncementEntry]:
    """从列表页 HTML 提取站内公告候选：文档序去重，截断到 ANNOUNCEMENT_MAX_ENTRIES。"""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    entries: list[AnnouncementEntry] = []
    seen_urls: set[str] = set()
    for m in _ANCHOR_RE.finditer(cleaned):
        url = urljoin(page_url, m.group(1).strip()).split("#", 1)[0]
        if url in seen_urls or not _host_allowed(url, official_domains):
            continue
        title = _clean_text(m.group(2))
        if len(title) < _TITLE_MIN_CHARS:
            continue
        seen_urls.add(url)
        entries.append(AnnouncementEntry(title, url, _find_date_scoped(m, cleaned)))
        if len(entries) >= ANNOUNCEMENT_MAX_ENTRIES:
            break
    return entries


# --------------------------------------------------------------------------
# json 模式（script 内嵌数据的 title+日期键对）
# --------------------------------------------------------------------------


def parse_json_blocks(
    html: str, page_url: str, official_domains: list[str]
) -> list[AnnouncementEntry]:
    """script JSON 里 (title|name, publishedOn|date|…) 对 → 条目；URL 回填列表页。"""
    scripts = " ".join(m.group(0) for m in re.finditer(r"<script\b.*?</script>", html, re.S | re.I))
    entries: list[AnnouncementEntry] = []
    seen_titles: set[str] = set()
    for m in _JSON_PAIR_RE.finditer(scripts):
        title, date = _clean_text(m.group(1)), m.group(2)
        if len(title) < _TITLE_MIN_CHARS or title in seen_titles:
            continue
        seen_titles.add(title)
        entries.append(AnnouncementEntry(title, page_url, date))
    return _cap(entries)


# --------------------------------------------------------------------------
# 源抓取入口
# --------------------------------------------------------------------------

_PARSERS = {
    "sitemap": parse_sitemap,
    "headings": parse_headings,
    "links": parse_links,
    "json": parse_json_blocks,
}


def fetch_listings(
    client: httpx.Client, vendor: VendorConfig
) -> tuple[list[AnnouncementEntry], list[str]]:
    """抓取厂商全部公告源并按各自 mode 解析合并（按 URL+标题去重，先到先得）。

    返回 (条目列表, 错误列表)；单源失败降级为记 error 继续其余源，
    全部失败返回 ([, 全部 error])——上层据此打印/告警，沿用昨日基线出数。
    """
    entries: list[AnnouncementEntry] = []
    seen_urls: set[str] = set()
    errors: list[str] = []
    for source in vendor.announcements:
        parser = _PARSERS[source.mode]
        try:
            resp = request_with_retries(client, "GET", source.url, follow_redirects=True)
            if source.mode == "sitemap":
                parsed = parse_sitemap(resp.text, source.url,
                                       vendor.official_domains, source.include)
            else:
                parsed = parser(resp.text, source.url, vendor.official_domains)
            for entry in parsed:
                key = (entry.url, entry.title.casefold())
                if key not in seen_urls:
                    seen_urls.add(key)
                    entries.append(entry)
        except Exception as exc:
            errors.append(f"{source.name}（{source.url}）：{exc!r}")
    return entries, errors
