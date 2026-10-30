"""官网公告源四模式解析器单测：fixture 钉住 anthropic/google/deepseek/zhipu/qwen 实测结构。"""

import httpx
import pytest
import respx

from free_model_spider.core.vendor_config import (
    AnnouncementSource,
    LineConfig,
    VendorConfig,
)
from free_model_spider.core.vendor_source import (
    ANNOUNCEMENT_MAX_ENTRIES,
    fetch_listings,
    parse_headings,
    parse_json_blocks,
    parse_links,
    parse_sitemap,
)

DOMAINS = ["z.ai", "example.com"]
PAGE = "https://www.example.com/news"


def parse(html: str, page: str = PAGE) -> list:
    return parse_links(html, page, DOMAINS)


# ---------- sitemap 模式（anthropic/minimax） ----------

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.example.com/news/old-post</loc><lastmod>2026-01-05T10:00:00+00:00</lastmod></url>
<url><loc>https://www.example.com/blog/new-model-x</loc><lastmod>2026-10-29</lastmod></url>
<url><loc>https://www.example.com/pricing</loc><lastmod>2026-10-28</lastmod></url>
<url><loc>https://external.com/news/elsewhere</loc><lastmod>2026-10-30</lastmod></url>
<url><loc>https://www.example.com/news/x#frag</loc></url>
</urlset>"""


def test_parse_sitemap_slug_title_lastmod_and_include_filter():
    entries = parse_sitemap(SITEMAP, "https://www.example.com/sitemap.xml",
                            DOMAINS, "/(news|blog)/")
    # 日期降序：sitemap 文档序与时间无关（常为字母序），降序保证截断保新
    assert [(e.title, e.date) for e in entries] == [
        ("new-model-x", "2026-10-29"),
        ("old-post", "2026-01-05"),
    ]
    # slug 保留连字符：门筛正则按连字符书写（minimax-m\d 实测教训）
    assert entries[0].url == "https://www.example.com/blog/new-model-x"


def test_parse_sitemap_without_include_keeps_all_and_sorts_dated_first():
    entries = parse_sitemap(SITEMAP, "https://www.example.com/sitemap.xml", DOMAINS, None)
    assert entries[0].date == "2026-10-29"  # 降序在前（blog/new-model-x）
    assert all("external.com" not in e.url for e in entries)


# ---------- headings 模式（deepseek h2日期+h3模型 / google strong / zhipu id日期） ----------

DEEPSEEK_STYLE = """<html><script>var x="2020-01-01";</script><body>
<h1>Change Log</h1>
<h2 id="date-2026-10-17">Date: 2026-10-17</h2>
<h3>DeepSeek-V4.1-Flash-1017 Update</h3><p>fixed bugs</p>
<h3>Opening up the search API</h3><p>docs</p>
<h2 id="date-2026-09-10">Date: 2026-09-10</h2>
<h3>DeepSeek-V4.1-Flash Release</h3><p>new model</p>
</body></html>"""


def test_parse_headings_deepseek_style_date_sections_with_model_children():
    entries = parse_headings(DEEPSEEK_STYLE, "https://api-docs.example.com/updates", DOMAINS)
    titles = {(e.title, e.date) for e in entries}
    # h3 模型名继承所属 h2 日期；script 内噪声日期不产出
    assert ("DeepSeek-V4.1-Flash-1017 Update", "2026-10-17") in titles
    assert ("Opening up the search API", "2026-10-17") in titles
    assert ("DeepSeek-V4.1-Flash Release", "2026-09-10") in titles
    assert not any(e.date == "2020-01-01" for e in entries)


GOOGLE_STYLE = """<html><body>
<h1>Release notes</h1>
<h2>October 27, 2026</h2>
<p><strong>Gemini 4 Argon ( gemini-4-argon ) generally available</strong></p>
<p><strong>CLI beta</strong></p>
<h2>October 13, 2026</h2>
<p><strong>older thing</strong></p>
</body></html>"""


def test_parse_headings_google_style_strong_phrases_as_titles():
    entries = parse_headings(GOOGLE_STYLE, "https://ai.example.com/changelog", DOMAINS)
    titles = {(e.title, e.date) for e in entries}
    assert ("Gemini 4 Argon ( gemini-4-argon ) generally available", "2026-10-27") in titles
    assert ("CLI beta", "2026-10-27") in titles
    assert ("older thing", "2026-10-13") in titles
    # 纯日期标题不直接成条目
    assert not any("October 27" in t for t, _ in titles)


ZHIPU_STYLE = """<html><body>
<h2>On this page</h2>
<ul><li><a href="#2026-08-26">2026-08-26</a></li></ul>
<div class="update" id="2026-08-26">
  <a href="/guides/vlm/glm-5.3-flash"><svg/></a>
</div>
<div class="update" id="2026-06-16">
  <a href="/guides/glm-5.2">GLM-5.2 发布</a>
</div>
</body></html>"""


def test_parse_headings_zhipu_style_id_date_sections_with_anchor_slugs():
    entries = parse_headings(ZHIPU_STYLE, "https://docs.example.com/release-notes", DOMAINS)
    titles = {(e.title, e.date) for e in entries}
    # 锚文本 "documentation" 不存在时图标锚退回 slug；文本锚拼接 "文本 slug"
    assert ("glm-5.3-flash", "2026-08-26") in titles
    assert ("GLM-5.2 发布 glm-5.2", "2026-06-16") in titles
    # 目录噪声（On this page / #锚）不成条目
    assert not any("On this page" in t for t, _ in titles)


def test_parse_headings_date_only_leaf_without_candidates_skipped():
    html = '<h2>2026-10-01</h2><p>no strong no anchor</p>'
    assert parse_headings(html, PAGE, DOMAINS) == []


# ---------- links 模式（qwen 博客类列表页） ----------

def test_parse_links_anchor_with_nearby_dates_and_domain_filter():
    html = """<html><script>{"d":"1999-01-01"}</script><body>
<a href='/blog/a.html'>Model A released</a><span title='2026-10-01 04:00:00 +0800'>October 1, 2026</span>
<a href="/blog/b">Model B blog</a> 2026-09-30
<a href="https://other.com/c">External C</a> 2026-10-05
<a href="/blog/a.html">dup</a>
</body></html>"""
    entries = parse(html)
    assert [(e.title, e.date) for e in entries] == [
        ("Model A released", "2026-10-01"),
        ("Model B blog", "2026-09-30"),
    ]


def test_parse_links_relative_url_joined_and_fragment_stripped():
    html = '<a href="/news/x#top">Some announcement title</a>'
    entries = parse(html)
    assert entries[0].url == "https://www.example.com/news/x"


def test_parse_links_short_titles_skipped_and_cap_applied():
    html = "".join(
        f'<a href="/p{i}">Title {i} something</a>' for i in range(70)
    )
    entries = parse(html)
    assert len(entries) == ANNOUNCEMENT_MAX_ENTRIES


# ---------- json 模式（anthropic script 数据） ----------

ANTHROPIC_STYLE = """<html><script>
window.data=[{\\"_id\\":\\"1\\",\\"title\\":\\"Introducing Claude Opus 5.5\\",
\\"publishedOn\\":\\"2026-10-15\\",\\"category\\":\\"announcements\\"},
{\\"title\\":\\"Research post\\",\\"publishedOn\\":\\"2026-10-10\\"}];
</script><body>static</body></html>"""


def test_parse_json_blocks_title_date_pairs_from_scripts():
    entries = parse_json_blocks(ANTHROPIC_STYLE, PAGE, DOMAINS)
    assert [(e.title, e.date, e.url) for e in entries] == [
        ("Introducing Claude Opus 5.5", "2026-10-15", PAGE),
        ("Research post", "2026-10-10", PAGE),
    ]


def test_parse_json_blocks_ignores_body_without_scripts():
    assert parse_json_blocks("<p>plain</p>", PAGE, DOMAINS) == []


# ---------- fetch_listings：多源合并去重与降级 ----------

VENDOR = VendorConfig(
    id="zai",
    name="智谱 AI",
    official_domains=DOMAINS,
    directory_slugs={"openrouter": ["z-ai"]},
    announcements=[
        AnnouncementSource(name="sitemap", url="https://www.example.com/sitemap.xml",
                           mode="sitemap", include="/(news|blog)/"),
        AnnouncementSource(name="changelog", url="https://www.example.com/updates",
                           mode="headings"),
    ],
    lines=[LineConfig(key="flagship", name="GLM 主线", match=[r"glm-\d"])],
)


@respx.mock
def test_fetch_listings_merges_and_dedupes_across_sources():
    respx.get("https://www.example.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text=SITEMAP))
    respx.get("https://www.example.com/updates").mock(
        return_value=httpx.Response(200, text=DEEPSEEK_STYLE))
    entries, errors = fetch_listings(httpx.Client(), VENDOR)
    assert errors == []
    # headings 模式条目共享列表页 URL，去重键是 (url, title)
    keys = [(e.url, e.title.casefold()) for e in entries]
    assert len(keys) == len(set(keys))
    assert any(e.date == "2026-10-29" for e in entries)  # sitemap 源
    assert any("DeepSeek-V4.1-Flash-1017" in e.title for e in entries)  # headings 源


@respx.mock
def test_fetch_listings_degrades_per_source_and_reports_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)  # 重试退避不真睡
    respx.get("https://www.example.com/sitemap.xml").mock(
        return_value=httpx.Response(500))
    respx.get("https://www.example.com/updates").mock(
        return_value=httpx.Response(200, text=DEEPSEEK_STYLE))
    entries, errors = fetch_listings(httpx.Client(), VENDOR)
    assert len(errors) == 1 and "sitemap" in errors[0]
    assert entries and all("DeepSeek" in e.title or "search API" in e.title
                           for e in entries)
