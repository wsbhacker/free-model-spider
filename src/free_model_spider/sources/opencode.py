import html
import re
from typing import ClassVar

import httpx

from free_model_spider.net import request_with_retries
from free_model_spider.sources.base import ModelRecord, Source, sort_records
from free_model_spider.sources.registry import register

DOCS_URL = "https://opencode.ai/docs/zen"
# 仅用于补全上下文/模态字段；免费判定 100% 以 DOCS_URL 官网定价表为准
MODELS_DEV_URL = "https://models.dev/api.json"

_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _table_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in _ROW_RE.findall(table_html):
        cells = [html.unescape(_TAG_RE.sub("", c)).strip() for c in _CELL_RE.findall(row_html)]
        if cells:
            rows.append(cells)
    return rows


def parse_tables(
    page_html: str,
) -> tuple[dict[str, str], list[dict[str, str]], set[str]]:
    """返回（模型名 → Model ID 映射，定价行列表，弃用模型名集合）。

    按表头识别表格而非按出现顺序：含 "Model ID" 的是模型表，
    含 "Input"+"Output" 的是定价表，含 "Deprecation" 的是弃用表。
    """
    name_to_id: dict[str, str] = {}
    pricing: list[dict[str, str]] = []
    deprecated: set[str] = set()
    for table in _TABLE_RE.findall(page_html):
        rows = _table_rows(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0]]
        body = rows[1:]
        if "model id" in header:
            idx = header.index("model id")
            name_to_id.update(
                {cells[0]: cells[idx] for cells in body if len(cells) > idx and cells[0]}
            )
        elif "input" in header and "output" in header:
            i, o = header.index("input"), header.index("output")
            cr = header.index("cached read") if "cached read" in header else None
            cw = header.index("cached write") if "cached write" in header else None
            for cells in body:
                if len(cells) <= max(i, o) or not cells[0]:
                    continue
                pricing.append({
                    "model": cells[0],
                    "input": cells[i],
                    "output": cells[o],
                    "cached_read": cells[cr] if cr is not None and len(cells) > cr else "",
                    "cached_write": cells[cw] if cw is not None and len(cells) > cw else "",
                })
        elif any("deprecation" in h for h in header):
            deprecated.update(cells[0] for cells in body if cells)
    return name_to_id, pricing, deprecated


def is_free(row: dict[str, str]) -> bool:
    return row["input"].strip().casefold() == "free" and row["output"].strip().casefold() == "free"


def normalize(
    name: str, model_id: str, pricing: dict[str, str], meta: dict | None = None,
) -> ModelRecord:
    # 官网表格不提供上下文长度/模态/介绍等字段，meta 来自 models.dev 补全，缺则留空
    meta = meta or {}
    return ModelRecord(
        source="opencode",
        id=model_id,
        name=name,
        context_length=meta.get("context_length"),
        input_modalities=meta.get("input_modalities", []),
        output_modalities=meta.get("output_modalities", []),
        links={"platform_page": DOCS_URL},
        raw={"pricing": pricing},
    )


def _fetch_modelsdev_metadata(client: httpx.Client) -> dict[str, dict]:
    resp = request_with_retries(client, "GET", MODELS_DEV_URL, timeout=60)
    models = (resp.json().get("opencode") or {}).get("models") or {}
    metadata: dict[str, dict] = {}
    for mid, m in models.items():
        limit = m.get("limit") or {}
        modalities = m.get("modalities") or {}
        metadata[mid] = {
            "context_length": limit.get("context"),
            "input_modalities": list(modalities.get("input") or []),
            "output_modalities": list(modalities.get("output") or []),
        }
    return metadata


@register
class OpenCodeSource(Source):
    name: ClassVar[str] = "opencode"
    display_name: ClassVar[str] = "OpenCode"

    def fetch_free_models(self) -> list[ModelRecord]:
        resp = request_with_retries(self.client, "GET", DOCS_URL, timeout=60)
        name_to_id, pricing, deprecated = parse_tables(resp.text)
        errors: list[str] = []
        if not pricing:
            errors.append("页面缺少定价表（表头需含 Input/Output），无法判定免费模型")
            self.last_errors = errors
            return []
        if not name_to_id:
            errors.append("页面缺少模型表（表头需含 Model ID），无法将定价行映射到 Model ID")
            self.last_errors = errors
            return []
        metadata: dict[str, dict] = {}
        try:
            metadata = _fetch_modelsdev_metadata(self.client)
            if not metadata:
                errors.append("models.dev 无 opencode 模型元数据，上下文/模态列将为未知")
        except Exception as exc:  # 第三方故障只降级不阻断官网免费列表
            errors.append(f"models.dev 元数据补全失败：{exc}")
        records: list[ModelRecord] = []
        for row in pricing:
            if not is_free(row) or row["model"] in deprecated:
                continue
            model_id = name_to_id.get(row["model"])
            if model_id is None:
                errors.append(f"{row['model']}: 定价表行未匹配到 Model ID")
                continue
            records.append(normalize(row["model"], model_id, row, metadata.get(model_id)))
        self.last_errors = errors
        return sort_records(records)
