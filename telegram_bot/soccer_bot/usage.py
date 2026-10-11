"""
Local SQLite store for natural-language questions: enforces the per-user quota and keeps
a log of what users ask, which feeds the backlog of new fixed commands (/sugerencias).
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS nl_questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    question      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|answered|unsupported|error|refunded
    tool          TEXT,
    params        TEXT,
    category      TEXT,
    summary       TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nl_chat_ts ON nl_questions (chat_id, ts);
"""

# Claude Opus 5.5 list prices (USD per token), used only for the /uso estimate
PRICE_INPUT = 4.00 / 1_000_000
PRICE_OUTPUT = 20.00 / 1_000_000


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    remaining: int
    resets_at: datetime | None  # when the oldest counted question leaves the window
    question_id: int | None = None


class UsageStore:
    def __init__(self, path: str, max_questions: int, window_hours: int):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.max_questions = max_questions
        self.window = timedelta(hours=window_hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _counted_in_window(self, chat_id: int, now: datetime) -> list[datetime]:
        since = (now - self.window).isoformat()
        rows = self._conn.execute(
            "SELECT ts FROM nl_questions WHERE chat_id = ? AND ts > ? AND status != 'refunded' ORDER BY ts",
            (chat_id, since),
        ).fetchall()
        return [datetime.fromisoformat(r["ts"]) for r in rows]

    def check(self, chat_id: int, now: datetime | None = None) -> QuotaStatus:
        now = now or self._now()
        with self._lock:
            used = self._counted_in_window(chat_id, now)
        remaining = max(0, self.max_questions - len(used))
        resets_at = used[0] + self.window if used else None
        return QuotaStatus(remaining > 0, remaining, resets_at)

    def reserve(self, chat_id: int, question: str, exempt: bool = False,
                now: datetime | None = None) -> QuotaStatus:
        """Atomically check the quota and record the question. Exempt users are logged but never blocked."""
        now = now or self._now()
        with self._lock:
            used = self._counted_in_window(chat_id, now)
            if not exempt and len(used) >= self.max_questions:
                return QuotaStatus(False, 0, used[0] + self.window)
            cursor = self._conn.execute(
                "INSERT INTO nl_questions (ts, chat_id, question) VALUES (?, ?, ?)",
                (now.isoformat(), chat_id, question),
            )
            self._conn.commit()
        remaining = max(0, self.max_questions - len(used) - 1)
        resets_at = (used[0] if used else now) + self.window
        return QuotaStatus(True, remaining, resets_at, cursor.lastrowid)

    def complete(self, question_id: int, status: str, tool: str | None = None,
                 params: dict | None = None, category: str | None = None,
                 summary: str | None = None, input_tokens: int | None = None,
                 output_tokens: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE nl_questions SET status = ?, tool = ?, params = ?, category = ?, summary = ?,
                   input_tokens = ?, output_tokens = ? WHERE id = ?""",
                (status, tool, json.dumps(params, ensure_ascii=False) if params else None,
                 category, summary, input_tokens, output_tokens, question_id),
            )
            self._conn.commit()

    def refund(self, question_id: int) -> None:
        """Give the question back to the user (our failure, not theirs)."""
        with self._lock:
            self._conn.execute("UPDATE nl_questions SET status = 'refunded' WHERE id = ?", (question_id,))
            self._conn.commit()

    def suggestions(self, limit: int = 10) -> dict:
        """Aggregates for deciding which fixed commands to add next."""
        with self._lock:
            categories = self._conn.execute(
                """SELECT category, count(*) AS n FROM nl_questions
                   WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC""",
            ).fetchall()
            tools = self._conn.execute(
                """SELECT tool, count(*) AS n FROM nl_questions
                   WHERE status = 'answered' GROUP BY tool ORDER BY n DESC""",
            ).fetchall()
            recent = self._conn.execute(
                """SELECT category, summary, question FROM nl_questions
                   WHERE category IS NOT NULL ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return {
            "categories": [dict(r) for r in categories],
            "tools": [dict(r) for r in tools],
            "recent": [dict(r) for r in recent],
        }

    def usage_since(self, days: int = 30) -> dict:
        since = (self._now() - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._conn.execute(
                """SELECT count(*) AS questions, count(DISTINCT chat_id) AS users,
                          coalesce(sum(input_tokens), 0) AS input_tokens,
                          coalesce(sum(output_tokens), 0) AS output_tokens
                   FROM nl_questions WHERE ts > ? AND status != 'refunded'""",
                (since,),
            ).fetchone()
        result = dict(row)
        result["cost_usd"] = result["input_tokens"] * PRICE_INPUT + result["output_tokens"] * PRICE_OUTPUT
        result["days"] = days
        return result
