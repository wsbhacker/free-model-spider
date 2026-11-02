"""厂商新鲜度 v4 配置：仅厂商身份；模型清单由状态文件承载（spec §4）。

v4 变更（spec 2026-11-02）：模型清单 / 公告源 / 目录 slug 全部移出配置，
旧字段（models / announcements / directory_slugs / manual）混入即报错。
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class VendorConfig(BaseModel):
    """单个厂商身份：id 为状态文件主键，official_domains 为证据 URL 白名单。"""

    model_config = ConfigDict(extra="forbid")  # v3 字段混入直接报错，不静默忽略

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    official_domains: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)  # 提问时附带的别名/中文名


class VendorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendors: list[VendorConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "VendorsConfig":
        ids = [vendor.id for vendor in self.vendors]
        if len(set(ids)) != len(ids):
            raise ValueError(f"vendor id 重复: {ids}")
        return self


def load_config(path: Path) -> VendorsConfig:
    """读取厂商配置；YAML 语法错误包装为带文件路径的 ValueError（沿用 v3）。"""
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML 解析失败: {exc}") from exc
    return VendorsConfig.model_validate(data)
