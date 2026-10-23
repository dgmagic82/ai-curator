"""SQLite request log and answer cache, shared by the Streamlit site and the Telegram bot.

Pipeline (one row per request, no redundant rows):
    request → validation → rejected (row with reason) | accepted → answer from cache or model → row «answered»
                                                                 → model failed → row «error»

Privacy, as the course requires: no names or usernames, the user id is a salted hash, phone numbers and emails
in questions are masked before they are stored, rows older than RETENTION_DAYS are deleted on start.
"""

import csv
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from curator import config

RETENTION_DAYS = 90
CACHE_TTL_DAYS = 7
SALT = os.environ.get("CURATOR_LOG_SALT", "local-dev-salt")  # set a secret salt on the server

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")

COLUMNS = ("id", "ts", "channel", "user_hash", "student_id", "level", "status", "reject_reason", "question",
           "answer", "category", "escalate", "sources", "from_cache", "response_time_ms", "tokens_in", "tokens_out",
           "model")


def user_hash(raw_id: str) -> str:
    return hashlib.sha256(f"{SALT}:{raw_id}".encode()).hexdigest()[:12]


def mask_pii(text: str) -> str:
    return _PHONE.sub("[телефон]", _EMAIL.sub("[email]", text))


def normalize(question: str) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", question.lower().replace("ё", "е")).split())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DatabaseLogger:
    def __init__(self, db_path: Path = config.DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    channel TEXT NOT NULL,            -- web | telegram | eval
                    user_hash TEXT NOT NULL,
                    student_id TEXT,                  -- demo student from the LMS stub
                    level TEXT,
                    status TEXT NOT NULL,             -- answered | rejected | error
                    reject_reason TEXT,
                    question TEXT NOT NULL,
                    answer TEXT,
                    category TEXT,
                    escalate INTEGER DEFAULT 0,
                    sources TEXT,                     -- JSON list of doc_ids
                    from_cache INTEGER DEFAULT 0,
                    response_time_ms INTEGER,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    model TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
                CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_hash);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    created TEXT NOT NULL,
                    result TEXT NOT NULL              -- JSON of the answer fields
                );
            """)
        self.purge()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def purge(self, now: datetime | None = None) -> int:
        """Retention: drop old log rows and stale cache entries. Returns the number of deleted log rows."""
        now = now or _now()
        with self._conn() as c:
            deleted = c.execute("DELETE FROM logs WHERE ts < ?",
                                ((now - timedelta(days=RETENTION_DAYS)).isoformat(),)).rowcount
            c.execute("DELETE FROM cache WHERE created < ?", ((now - timedelta(days=CACHE_TTL_DAYS)).isoformat(),))
        return deleted

    def log(self, *, channel: str, user_ref: str, question: str, status: str, student_id: str | None = None,
            level: str | None = None, reject_reason: str | None = None, answer: str | None = None,
            category: str | None = None, escalate: bool = False, sources: list[str] | None = None,
            from_cache: bool = False, response_time_ms: int | None = None, tokens_in: int = 0,
            tokens_out: int = 0, model: str | None = None, ts: datetime | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO logs ({', '.join(COLUMNS[1:])}) VALUES ({', '.join('?' * (len(COLUMNS) - 1))})",
                ((ts or _now()).isoformat(timespec="seconds"), channel, user_hash(user_ref), student_id, level,
                 status, reject_reason, mask_pii(question), answer, category, int(escalate),
                 json.dumps(sources or [], ensure_ascii=False), int(from_cache), response_time_ms, tokens_in,
                 tokens_out, model))
            return cur.lastrowid

    def count_today(self, user_ref: str, channel: str) -> int:
        """Answered requests of one user today (UTC), for the per-user daily limit."""
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM logs WHERE user_hash = ? AND channel = ? AND status = 'answered' "
                             "AND ts >= ?", (user_hash(user_ref), channel, start)).fetchone()[0]

    def rows(self, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM logs ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql)]

    def get_stats(self) -> dict:
        with self._conn() as c:
            q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
            answered = "status = 'answered'"
            stats = {
                "total": q("SELECT COUNT(*) FROM logs"),
                "answered": q(f"SELECT COUNT(*) FROM logs WHERE {answered}"),
                "rejected": dict(c.execute("SELECT reject_reason, COUNT(*) FROM logs WHERE status = 'rejected' "
                                           "GROUP BY reject_reason").fetchall()),
                "errors": q("SELECT COUNT(*) FROM logs WHERE status = 'error'"),
                "users": q("SELECT COUNT(DISTINCT user_hash) FROM logs"),
                "by_channel": dict(c.execute("SELECT channel, COUNT(*) FROM logs GROUP BY channel").fetchall()),
                "by_category": dict(c.execute(f"SELECT category, COUNT(*) FROM logs WHERE {answered} "
                                              "GROUP BY category ORDER BY 2 DESC").fetchall()),
                "from_cache": q(f"SELECT COUNT(*) FROM logs WHERE {answered} AND from_cache = 1"),
                "escalated": q(f"SELECT COUNT(*) FROM logs WHERE {answered} AND escalate = 1"),
                "no_source": q(f"SELECT COUNT(*) FROM logs WHERE {answered} AND sources = '[]' "
                               "AND category != 'вне рамок'"),
                "avg_ms": q(f"SELECT AVG(response_time_ms) FROM logs WHERE {answered} AND from_cache = 0") or 0,
                "tokens_in": q("SELECT COALESCE(SUM(tokens_in), 0) FROM logs"),
                "tokens_out": q("SELECT COALESCE(SUM(tokens_out), 0) FROM logs"),
            }
            times = sorted(r[0] for r in c.execute(f"SELECT response_time_ms FROM logs WHERE {answered} "
                                                   "AND from_cache = 0 AND response_time_ms IS NOT NULL"))
        stats["p95_ms"] = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0
        # gpt-4.1-mini prices on OpenRouter, $ per 1M tokens (see docs/ARCHITECTURE.md)
        stats["cost_usd"] = round(stats["tokens_in"] * 0.4e-6 + stats["tokens_out"] * 1.6e-6, 4)
        return stats

    def export_csv(self, path: Path) -> Path:
        rows = self.rows()
        with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return Path(path)

    # --- answer cache ---
    def cache_get(self, key: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT created, result FROM cache WHERE key = ?", (key,)).fetchone()
        if not row or datetime.fromisoformat(row["created"]) < _now() - timedelta(days=CACHE_TTL_DAYS):
            return None
        return json.loads(row["result"])

    def cache_put(self, key: str, result: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO cache (key, created, result) VALUES (?, ?, ?)",
                      (key, _now().isoformat(), json.dumps(result, ensure_ascii=False)))
