"""Transactional review schedules and immutable result events."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock

from app.learning.review import review_is_due, schedule_review, utc_datetime, validate_review_score


class ReviewConflict(ValueError):
    """The result refers to stale state or a reused event with new contents."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return utc_datetime(value).isoformat(timespec="microseconds")


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


class ReviewStore:
    def __init__(self, path: str | Path, clock=utc_now):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = RLock()
        self.connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS review_items (
                item_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                success_streak INTEGER NOT NULL, interval_days INTEGER NOT NULL,
                reviewed_at TEXT NOT NULL, next_review_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_events (
                event_id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
                payload TEXT NOT NULL, response TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS review_due_index ON review_items(next_review_at, item_id);
        """)
        self.connection.commit()

    def close(self):
        with self.lock:
            self.connection.close()

    def record(
        self, item_id: str, event_id: str, result: dict, *, title: str,
        reviewed_at: datetime | None = None,
        expected_review_at: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        """Persist one result once, optionally within the caller's transaction.

        expected_review_at guards an explicit due-review submission. Replayed
        events return their first response even if the current clock has moved.
        """
        item_id = _identifier(item_id, "item_id")
        event_id = _identifier(event_id, "event_id")
        title = _identifier(title, "title")
        if not isinstance(result, dict):
            raise ValueError("result 必须是判分字典")
        completed_at = utc_datetime(self.clock() if reviewed_at is None else reviewed_at)
        validate_review_score(result.get("score"), result.get("max_score"))
        expected = None if expected_review_at is None else _timestamp(expected_review_at)
        try:
            payload = json.dumps({"item_id": item_id, "result": result, "expected_review_at": expected},
                                 ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ValueError("判分结果必须可以保存为 JSON") from error
        result = json.loads(payload)["result"]
        if connection is not None:
            if not connection.in_transaction:
                raise ValueError("外部连接必须已经开启写事务")
            return self._record(connection, item_id, event_id, result, title, completed_at, expected, payload)
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                response = self._record(self.connection, item_id, event_id, result, title, completed_at, expected, payload)
                self.connection.commit()
                return response
            except Exception:
                self.connection.rollback()
                raise

    def _record(self, connection, item_id, event_id, result, title, completed_at, expected, payload):
        event = connection.execute("SELECT payload,response FROM review_events WHERE event_id=?", (event_id,)).fetchone()
        if event:
            if event[0] != payload:
                raise ReviewConflict("同一复习记录编号的内容已改变")
            return json.loads(event[1])
        previous = connection.execute(
            "SELECT success_streak,reviewed_at,next_review_at FROM review_items WHERE item_id=?", (item_id,),
        ).fetchone()
        streak = 0
        if previous:
            streak = previous[0]
            if completed_at < datetime.fromisoformat(previous[1]):
                raise ReviewConflict("完成时间早于上一条记录，请检查系统时间")
        if expected is not None:
            if previous is None or previous[2] != expected:
                raise ReviewConflict("复习计划已变化，请刷新后重试")
            if not review_is_due(datetime.fromisoformat(expected), completed_at):
                raise ReviewConflict("该题尚未到复习时间")
        schedule = schedule_review(result["score"], result["max_score"], streak, completed_at)
        value = {
            "item_id": item_id, "title": title,
            "success_streak": schedule["success_streak"], "interval_days": schedule["interval_days"],
            "reviewed_at": _timestamp(completed_at), "next_review_at": _timestamp(schedule["next_review_at"]),
        }
        response = {"schedule": value, "result": result}
        connection.execute("""
            INSERT INTO review_items VALUES (?,?,?,?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET title=excluded.title,
                success_streak=excluded.success_streak, interval_days=excluded.interval_days,
                reviewed_at=excluded.reviewed_at, next_review_at=excluded.next_review_at
        """, (item_id, title, value["success_streak"], value["interval_days"], value["reviewed_at"], value["next_review_at"]))
        connection.execute("INSERT INTO review_events VALUES (?,?,?,?)",
                           (event_id, item_id, payload, json.dumps(response, ensure_ascii=False)))
        return json.loads(json.dumps(response, ensure_ascii=False))

    def list_items(self, *, due_only: bool = True, now: datetime | None = None) -> dict:
        current = utc_datetime(self.clock() if now is None else now)
        with self.lock:
            sql = "SELECT * FROM review_items"
            parameters = ()
            if due_only:
                sql += " WHERE next_review_at <= ?"
                parameters = (_timestamp(current),)
            rows = self.connection.execute(sql + " ORDER BY next_review_at,item_id", parameters).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["is_due"] = review_is_due(datetime.fromisoformat(item["next_review_at"]), current)
                items.append(item)
            return {"server_time": _timestamp(current), "items": items}
