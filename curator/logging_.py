"""JSONL sample log of the eval run: the analytics page shows it while the live SQLite log is empty."""

import json
from datetime import datetime, timezone
from pathlib import Path


def log_query(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record},
                           ensure_ascii=False) + "\n")


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
