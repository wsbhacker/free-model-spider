"""厂商新鲜度统计的配置模型与加载（v3：人工圈定模型清单）。"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManualRef(BaseModel):
    """人工纠偏兜底（v3 spec §3）：配置即 confirmed（source=manual），不参与日常提取。

    日常形态只维护模型清单（match/exclude）；manual 仅在管线提错日期时钉死真值用。
    """

    model_config = ConfigDict(extra="forbid")  # yml 拼错字段名直接报错，不静默忽略

    date: str
    url: str


class ModelConfig(BaseModel):
    """单个已维护模型：match/exclude 是公告条目标题与目录 id 的窄匹配
    （regex any-of + exclude 子串剔除，text 已 casefold、目录 id 保留连字符与 ':'）。
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    match: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)
    manual: ManualRef | None = None


class AnnouncementSource(BaseModel):
    """官方公告源：官网爬取日期提取通道的数据入口（四模式，沿用 v2 spec §3/§4）。

    mode 决定解析器：sitemap（loc+lastmod）/ headings（日期节+模型子节）/
    links（锚点+邻近日期）/ json（script 内 title+日期键对）。
    include 仅 sitemap 模式用：URL path 过滤正则（如 ``/news/``）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    mode: Literal["sitemap", "headings", "links", "json"] = "links"
    include: str | None = None


class VendorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    official_domains: list[str] = Field(min_length=1)
    directory_slugs: dict[str, list[str]] = Field(min_length=1)
    models: list[ModelConfig] = Field(min_length=1)
    announcements: list[AnnouncementSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_models(self) -> "VendorConfig":
        keys = [model.key for model in self.models]
        if len(set(keys)) != len(keys):
            raise ValueError(f"model key 重复: {keys}")
        return self


class VendorReleasesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendors: list[VendorConfig] = Field(min_length=1)


def load_config(path: Path) -> VendorReleasesConfig:
    """读取厂商新鲜度统计配置；YAML 语法错误包装为带文件路径的 ValueError。"""
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML 解析失败: {exc}") from exc
    return VendorReleasesConfig.model_validate(data)
