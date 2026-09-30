import json
from pathlib import Path

from free_model_spider.sources.base import ModelRecord, sort_records


def snapshot_path(data_dir: Path, source: str, date: str) -> Path:
    return data_dir / "snapshots" / source / f"{date}.json"


def save_snapshot(
    records: list[ModelRecord], path: Path, source: str, date: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "date": date,
        "count": len(records),
        "models": [r.model_dump(mode="json") for r in sort_records(records)],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_snapshot(path: Path) -> list[ModelRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ModelRecord.model_validate(m) for m in data["models"]]


def find_latest_snapshot(data_dir: Path, source: str, before_date: str) -> Path | None:
    directory = data_dir / "snapshots" / source
    if not directory.is_dir():
        return None
    candidates = sorted(
        p for p in directory.glob("*.json") if p.stem < before_date
    )
    return candidates[-1] if candidates else None
