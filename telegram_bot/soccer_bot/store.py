"""
Local SQLite store: users, subscriptions, prepaid question packs and the natural-language
question log (quota accounting, backlog of new commands, cost and revenue metrics).

Each question is charged to exactly one source, in this order:
  admin -> subscription (monthly allowance, optional daily cap) -> prepaid pack -> free
  (welcome questions, then weekly allowance).
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from soccer_bot.plans import FREE_TIER, Catalog

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    plan        TEXT NOT NULL,
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    questions   INTEGER NOT NULL,
    price_paid  INTEGER NOT NULL,
    founder     INTEGER NOT NULL DEFAULT 0,
    reminded    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_chat ON subscriptions (chat_id, ends_at);
CREATE TABLE IF NOT EXISTS credits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    pack       TEXT NOT NULL,
    questions  INTEGER NOT NULL,
    price_paid INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nl_questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    question      TEXT NOT NULL,
    source        TEXT NOT NULL,  -- admin|subscription|pack|free_welcome|free_weekly
    subscription_id INTEGER,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|answered|unsupported|locked|error|refunded
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
WEEK = timedelta(days=7)
DAY = timedelta(days=1)


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    source: str | None = None
    question_id: int | None = None
    reason: str | None = None  # when denied: free_exhausted | plan_exhausted | daily_cap
    retry_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: str, catalog: Catalog):
        self.catalog = catalog
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def _count(self, sql: str, params: tuple = ()) -> int:
        return self._conn.execute(sql, params).fetchone()[0]

    # --- users ---

    def touch_user(self, chat_id: int, username: str | None, first_name: str | None,
                   now: datetime | None = None) -> None:
        ts = (now or _now()).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO users (chat_id, username, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (chat_id) DO UPDATE SET username = excluded.username,
                   first_name = excluded.first_name, last_seen = excluded.last_seen""",
                (chat_id, username, first_name, ts, ts),
            )
            self._conn.commit()

    def user(self, chat_id: int) -> dict | None:
        with self._lock:
            rows = self._q("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        return dict(rows[0]) if rows else None

    # --- subscriptions ---

    def active_subscription(self, chat_id: int, now: datetime | None = None) -> dict | None:
        ts = (now or _now()).isoformat()
        with self._lock:
            rows = self._q(
                """SELECT * FROM subscriptions WHERE chat_id = ? AND starts_at <= ? AND ends_at > ?
                   ORDER BY ends_at DESC LIMIT 1""",
                (chat_id, ts, ts),
            )
        return dict(rows[0]) if rows else None

    def tier(self, chat_id: int, now: datetime | None = None) -> str:
        sub = self.active_subscription(chat_id, now)
        return sub["plan"] if sub else FREE_TIER

    def activate(self, chat_id: int, plan_id: str, periods: int = 1, founder: bool = False,
                 now: datetime | None = None) -> dict:
        """Start or renew a plan. A renewal of the same plan starts when the current one ends."""
        plan = self.catalog.plan(plan_id)
        if plan is None:
            raise ValueError(f"Unknown plan: {plan_id}")
        if founder and not self.catalog.founder_offer:
            raise ValueError("There is no founder offer configured")
        now = now or _now()
        price = self.catalog.founder_offer.price if founder else plan.price
        current = self.active_subscription(chat_id, now)
        if current and current["plan"] == plan_id:
            starts = _dt(current["ends_at"])
        else:
            starts = now
            if current:  # plan change takes effect now, the old one is closed
                self.deactivate(chat_id, now)
        with self._lock:
            created = None
            for i in range(periods):
                period_start = starts + timedelta(days=plan.days * i)
                cursor = self._conn.execute(
                    """INSERT INTO subscriptions (chat_id, plan, starts_at, ends_at, questions, price_paid,
                       founder, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (chat_id, plan_id, period_start.isoformat(),
                     (period_start + timedelta(days=plan.days)).isoformat(),
                     plan.monthly_questions, price, int(founder), now.isoformat()),
                )
                created = created or cursor.lastrowid
            self._conn.commit()
            row = self._q("SELECT * FROM subscriptions WHERE id = ?", (created,))[0]
        result = dict(row)
        result["ends_at_final"] = (starts + timedelta(days=plan.days * periods)).isoformat()
        return result

    def deactivate(self, chat_id: int, now: datetime | None = None) -> int:
        """End current and future periods now. Returns how many periods were affected."""
        ts = (now or _now()).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE subscriptions SET ends_at = ? WHERE chat_id = ? AND ends_at > ? AND starts_at <= ?",
                (ts, chat_id, ts, ts),
            )
            future = self._conn.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND starts_at > ?", (chat_id, ts),
            )
            self._conn.commit()
        return cursor.rowcount + future.rowcount

    def founder_slots_used(self) -> int:
        with self._lock:
            return self._count("SELECT count(DISTINCT chat_id) FROM subscriptions WHERE founder = 1")

    def due_for_reminder(self, days_before: int, now: datetime | None = None) -> list[dict]:
        """Subscriptions ending soon with no renewal queued and no reminder sent yet."""
        now = now or _now()
        with self._lock:
            rows = self._q(
                """SELECT s.* FROM subscriptions s
                   WHERE s.reminded = 0 AND s.ends_at > ? AND s.ends_at <= ? AND s.starts_at <= ?
                     AND NOT EXISTS (SELECT 1 FROM subscriptions n
                                     WHERE n.chat_id = s.chat_id AND n.starts_at >= s.ends_at)""",
                (now.isoformat(), (now + timedelta(days=days_before)).isoformat(), now.isoformat()),
            )
        return [dict(r) for r in rows]

    def mark_reminded(self, subscription_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE subscriptions SET reminded = 1 WHERE id = ?", (subscription_id,))
            self._conn.commit()

    # --- prepaid packs ---

    def add_pack(self, chat_id: int, pack_id: str, now: datetime | None = None) -> int:
        pack = self.catalog.packs.get(pack_id)
        if pack is None:
            raise ValueError(f"Unknown pack: {pack_id}")
        with self._lock:
            self._conn.execute(
                "INSERT INTO credits (chat_id, pack, questions, price_paid, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, pack.id, pack.questions, pack.price, (now or _now()).isoformat()),
            )
            self._conn.commit()
        return self.pack_balance(chat_id)

    def pack_balance(self, chat_id: int) -> int:
        with self._lock:
            bought = self._count("SELECT coalesce(sum(questions), 0) FROM credits WHERE chat_id = ?", (chat_id,))
            used = self._count(
                "SELECT count(*) FROM nl_questions WHERE chat_id = ? AND source = 'pack' AND status != 'refunded'",
                (chat_id,),
            )
        return max(0, bought - used)

    # --- question accounting ---

    def _used(self, chat_id: int, source: str, since: datetime | None = None,
              subscription_id: int | None = None) -> list[datetime]:
        sql = "SELECT ts FROM nl_questions WHERE chat_id = ? AND source = ? AND status != 'refunded'"
        params: list = [chat_id, source]
        if since is not None:
            sql += " AND ts > ?"
            params.append(since.isoformat())
        if subscription_id is not None:
            sql += " AND subscription_id = ?"
            params.append(subscription_id)
        return [_dt(r["ts"]) for r in self._q(sql + " ORDER BY ts", tuple(params))]

    def _pick_source(self, chat_id: int, now: datetime, exempt: bool) -> tuple[str | None, int | None, str | None, datetime | None]:
        """Returns (source, subscription_id, deny_reason, retry_at)."""
        if exempt:
            return "admin", None, None, None

        sub = self.active_subscription(chat_id, now)
        if sub:
            plan = self.catalog.plan(sub["plan"])
            today = self._used(chat_id, "subscription", since=now - DAY)
            if plan and plan.daily_cap and len(today) >= plan.daily_cap:
                return None, None, "daily_cap", today[0] + DAY
            if len(self._used(chat_id, "subscription", subscription_id=sub["id"])) < sub["questions"]:
                return "subscription", sub["id"], None, None

        if self.pack_balance(chat_id) > 0:
            return "pack", None, None, None

        if sub:
            return None, None, "plan_exhausted", _dt(sub["ends_at"])

        if len(self._used(chat_id, "free_welcome")) < self.catalog.welcome_questions:
            return "free_welcome", None, None, None
        weekly = self._used(chat_id, "free_weekly", since=now - WEEK)
        if len(weekly) < self.catalog.weekly_questions:
            return "free_weekly", None, None, None
        return None, None, "free_exhausted", (weekly[0] + WEEK) if weekly else None

    def reserve(self, chat_id: int, question: str, exempt: bool = False,
                now: datetime | None = None) -> Reservation:
        """Atomically pick the source that pays for this question and record it."""
        now = now or _now()
        with self._lock:
            source, sub_id, reason, retry_at = self._pick_source(chat_id, now, exempt)
            if source is None:
                return Reservation(False, reason=reason, retry_at=retry_at)
            cursor = self._conn.execute(
                "INSERT INTO nl_questions (ts, chat_id, question, source, subscription_id) VALUES (?, ?, ?, ?, ?)",
                (now.isoformat(), chat_id, question, source, sub_id),
            )
            self._conn.commit()
        return Reservation(True, source, cursor.lastrowid)

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

    def account(self, chat_id: int, now: datetime | None = None) -> dict:
        """Everything /micuenta shows."""
        now = now or _now()
        with self._lock:
            sub = self.active_subscription(chat_id, now)
            info = {"tier": sub["plan"] if sub else FREE_TIER, "subscription": sub,
                    "pack_balance": self.pack_balance(chat_id)}
            if sub:
                plan = self.catalog.plan(sub["plan"])
                used = len(self._used(chat_id, "subscription", subscription_id=sub["id"]))
                renewal = self._q(
                    "SELECT max(ends_at) AS ends FROM subscriptions WHERE chat_id = ? AND starts_at >= ?",
                    (chat_id, sub["starts_at"]),
                )[0]["ends"]
                info |= {"plan_remaining": max(0, sub["questions"] - used), "plan_questions": sub["questions"],
                         "ends_at": _dt(renewal),
                         "daily_cap": plan.daily_cap if plan else None,
                         "daily_used": len(self._used(chat_id, "subscription", since=now - DAY))}
            else:
                welcome_left = max(0, self.catalog.welcome_questions - len(self._used(chat_id, "free_welcome")))
                weekly = self._used(chat_id, "free_weekly", since=now - WEEK)
                info |= {"welcome_left": welcome_left,
                         "weekly_left": max(0, self.catalog.weekly_questions - len(weekly)),
                         "weekly_next_at": (weekly[0] + WEEK) if weekly else None}
        return info

    # --- admin reports ---

    def suggestions(self, limit: int = 10) -> dict:
        """Aggregates for deciding which fixed commands to add next."""
        with self._lock:
            categories = self._q(
                """SELECT category, count(*) AS n FROM nl_questions
                   WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC""")
            tools = self._q(
                """SELECT tool, count(*) AS n FROM nl_questions
                   WHERE status IN ('answered', 'locked') GROUP BY tool ORDER BY n DESC""")
            recent = self._q(
                """SELECT category, summary, question FROM nl_questions
                   WHERE category IS NOT NULL ORDER BY id DESC LIMIT ?""", (limit,))
        return {"categories": [dict(r) for r in categories], "tools": [dict(r) for r in tools],
                "recent": [dict(r) for r in recent]}

    def usage_since(self, days: int = 30, now: datetime | None = None) -> dict:
        now = now or _now()
        since = (now - timedelta(days=days)).isoformat()
        ts = now.isoformat()
        with self._lock:
            row = dict(self._q(
                """SELECT count(*) AS questions, count(DISTINCT chat_id) AS askers,
                          coalesce(sum(input_tokens), 0) AS input_tokens,
                          coalesce(sum(output_tokens), 0) AS output_tokens
                   FROM nl_questions WHERE ts > ? AND status != 'refunded'""", (since,))[0])
            active = self._q(
                """SELECT plan, count(DISTINCT chat_id) AS n, sum(price_paid) AS revenue FROM subscriptions
                   WHERE starts_at <= ? AND ends_at > ? GROUP BY plan""", (ts, ts))
            row["users"] = self._count("SELECT count(*) FROM users")
            row["active_by_plan"] = {r["plan"]: r["n"] for r in active}
            row["mrr"] = sum(r["revenue"] or 0 for r in active)
            row["revenue"] = (
                self._count("SELECT coalesce(sum(price_paid), 0) FROM subscriptions WHERE created_at > ?", (since,))
                + self._count("SELECT coalesce(sum(price_paid), 0) FROM credits WHERE created_at > ?", (since,))
            )
            row["founders"] = self.founder_slots_used()
        row["cost_usd"] = row["input_tokens"] * PRICE_INPUT + row["output_tokens"] * PRICE_OUTPUT
        row["days"] = days
        return row

    def active_subscribers(self, now: datetime | None = None) -> list[dict]:
        ts = (now or _now()).isoformat()
        with self._lock:
            rows = self._q(
                """SELECT s.chat_id, s.plan, s.ends_at, s.founder, u.username, u.first_name
                   FROM subscriptions s LEFT JOIN users u USING (chat_id)
                   WHERE s.starts_at <= ? AND s.ends_at > ? ORDER BY s.ends_at""", (ts, ts))
        return [dict(r) for r in rows]
