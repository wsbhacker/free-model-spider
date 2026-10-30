"""厂商大模型新鲜度统计的配置模型与加载。"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class Override(BaseModel):
    """人工纠偏兜底：配置后该线直接采用其值，跳过自动检测。"""

    model: str
    release_date: str
    source: str


class AnnouncementSource(BaseModel):
    """官方公告源：官网爬取主通道的数据入口（v2 spec §3/§4）。

    mode 决定解析器：sitemap（loc+lastmod）/ headings（日期节+模型子节）/
    links（锚点+邻近日期）/ json（script 内 title+日期键对）。
    include 仅 sitemap 模式用：URL path 过滤正则（如 ``/news/``）。
    """

    name: str
    url: str
    mode: Literal["sitemap", "headings", "links", "json"] = "links"
    include: str | None = None


class LineConfig(BaseModel):
    """产品线定义：match/exclude 是公告条目与目录条目的门筛（regex，any-of，按线独立）。"""

    key: Literal["flagship", "sub"]
    name: str
    match: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)
    override: Override | None = None


class VendorConfig(BaseModel):
    id: str
    name: str
    official_domains: list[str] = Field(min_length=1)
    directory_slugs: dict[str, list[str]] = Field(min_length=1)
    lines: list[LineConfig] = Field(min_length=1)
    announcements: list[AnnouncementSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lines(self) -> "VendorConfig":
        keys = [line.key for line in self.lines]
        if keys[0] != "flagship":
            raise ValueError("首个 line 的 key 必须是 flagship")
        if len(set(keys)) != len(keys):
            raise ValueError(f"line key 重复: {keys}")
        return self


class VendorReleasesConfig(BaseModel):
    stale_days: int = 45
    vendors: list[VendorConfig] = Field(min_length=1)


def load_config(path: Path) -> VendorReleasesConfig:
    """读取厂商新鲜度统计配置；YAML 语法错误包装为带文件路径的 ValueError。"""
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML 解析失败: {exc}") from exc
    return VendorReleasesConfig.model_validate(data)
