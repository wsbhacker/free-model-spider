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


class LineConfig(BaseModel):
    """产品线定义：match/exclude 是目录门铃的粗筛（regex，any-of）。"""

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
