"""Query log: one JSON line per question, the input for the analytics page."""

import json
from datetime import datetime, timezone
from pathlib import Path

from curator import config


def log_query(record: dict, path: Path = config.LOG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record},
                           ensure_ascii=False) + "\n")


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
