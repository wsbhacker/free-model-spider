import json

from pydantic import BaseModel

from free_model_spider.sources.base import ModelRecord, sort_records


class DiffResult(BaseModel):
    added: list[ModelRecord]
    removed: list[ModelRecord]
    unchanged: list[ModelRecord]


def diff_models(today: list[ModelRecord], yesterday: list[ModelRecord]) -> DiffResult:
    today_by = {r.id: r for r in today}
    yesterday_by = {r.id: r for r in yesterday}
    added = sort_records([today_by[i] for i in today_by.keys() - yesterday_by.keys()])
    removed = sort_records([yesterday_by[i] for i in yesterday_by.keys() - today_by.keys()])
    unchanged = sort_records([today_by[i] for i in today_by.keys() & yesterday_by.keys()])
    return DiffResult(added=added, removed=removed, unchanged=unchanged)


DIFF_JSON_SCHEMA_VERSION = 1


def dump_diff_json(
    diff: DiffResult,
    *,
    source: str,
    display_name: str,
    date: str,
    baseline_date: str | None,
    total: int,
) -> str:
    # raw 是平台原始 payload，剔除以保持对外契约稳定（schema_version 管演进）
    def slim(r: ModelRecord) -> dict:
        d = r.model_dump(mode="json", exclude={"raw"})
        d["page_url"] = r.page_url
        return d

    payload = {
        "schema_version": DIFF_JSON_SCHEMA_VERSION,
        "source": source,
        "display_name": display_name,
        "date": date,
        "baseline_date": baseline_date,
        "total": total,
        "added": [slim(r) for r in sort_records(diff.added)],
        "removed": [slim(r) for r in sort_records(diff.removed)],
        "unchanged": [slim(r) for r in sort_records(diff.unchanged)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
