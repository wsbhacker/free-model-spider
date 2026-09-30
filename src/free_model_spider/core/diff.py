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
