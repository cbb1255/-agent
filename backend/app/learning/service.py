"""Single-worker durable session execution with idempotent requests."""
import hashlib
import json
import logging
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from threading import Condition, RLock
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.graph.learning import build_learning_graph, public_quiz
from app.graph.nodes.grade import grade_quiz
from app.graph.session import build_thread_config
from app.learning.review_store import ReviewConflict, ReviewStore
from app.learning.progress import summarize_concept_progress, summarize_learning
from app.learning.mastery import assess_concept_mastery
from app.learning.review import utc_datetime, validate_review_score
from app.llm import drain_model_calls, sum_usage, request_usage, usage_request
from app.rag.retriever import to_citations

LOGGER = logging.getLogger(__name__)


class LearningError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class LearningService:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._idle = Condition(self.lock)
        self._session_locks = {}
        self._active_operations = 0
        self._closing = False
        self._closed = False
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS learning_schema (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO learning_schema VALUES (1);
            CREATE TABLE IF NOT EXISTS learning_sessions (
                id TEXT PRIMARY KEY, cancelled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS learning_requests (
                session_id TEXT, request_id TEXT, payload TEXT NOT NULL,
                baseline TEXT, result TEXT, PRIMARY KEY (session_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS exercises (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, task_id TEXT NOT NULL,
                number INTEGER NOT NULL, quiz TEXT NOT NULL,
                UNIQUE (session_id, task_id, number)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                exercise_id TEXT PRIMARY KEY, answer TEXT NOT NULL, result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_concepts (
                id TEXT PRIMARY KEY, label TEXT NOT NULL,
                normalized_key TEXT NOT NULL UNIQUE, origin TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_exercise_metadata (
                exercise_id TEXT PRIMARY KEY, concept_id TEXT NOT NULL, source_refs TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_teaching_events (
                event_id TEXT PRIMARY KEY, concept_id TEXT NOT NULL,
                source_refs TEXT NOT NULL, taught_at TEXT NOT NULL
            );
        """)
        self.connection.commit()
        version = self.connection.execute("SELECT MAX(version) FROM learning_schema").fetchone()[0]
        if version != 1:
            self.connection.close()
            raise RuntimeError("学习数据库版本不受支持")
        self.saver_connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.saver = SqliteSaver(self.saver_connection)
        self.reviews = ReviewStore(path)
        self.graph = build_learning_graph(self, self.saver)

    def close(self):
        with self._idle:
            self._closing = True
            while self._active_operations:
                self._idle.wait()
            if self._closed:
                return
            with self.saver.lock:
                self.saver_connection.close()
            self.reviews.close()
            self.connection.close()
            self._closed = True

    def _ensure_open(self):
        if self._closed:
            raise LearningError("学习服务已关闭，请重新连接。", 503)

    @contextmanager
    def _activity(self, *, new_request=False):
        with self._idle:
            self._ensure_open()
            if new_request and self._closing:
                raise LearningError("学习服务正在关闭，请稍后重试。", 503)
            self._active_operations += 1
        try:
            yield
        finally:
            with self._idle:
                self._active_operations -= 1
                self._idle.notify_all()

    def _session_lock(self, session_id):
        with self.lock:
            self._ensure_open()
            return self._session_locks.setdefault(session_id, RLock())

    def create(self) -> dict:
        with self._activity(new_request=True):
            session_id = uuid4().hex
            with self.lock, self.connection:
                self.connection.execute("INSERT INTO learning_sessions(id) VALUES (?)", (session_id,))
            return self.view(session_id)

    def _session(self, session_id):
        self._ensure_open()
        row = self.connection.execute(
            "SELECT cancelled FROM learning_sessions WHERE id = ?", (session_id,),
        ).fetchone()
        if row is None:
            raise LearningError("会话不存在，请新建会话。", 404)
        return row

    def view(self, session_id) -> dict:
        """Return a best-effort session snapshot without waiting for an in-flight
        operation on the same session; cancellation and actions remain coherent
        through the database flag and the checkpoint store."""
        with self._activity():
            with self.lock:
                session = self._session(session_id)
            snapshot = self.graph.get_state(build_thread_config(session_id))
            state = snapshot.values
            pending = None
            if snapshot.interrupts and not session["cancelled"]:
                pending = snapshot.interrupts[0].value
            quiz = state.get("quiz")
            can_retry = bool(snapshot.next) and not pending and not session["cancelled"]
            retry_request = None
            if can_retry:
                with self.lock:
                    row = self.connection.execute(
                        "SELECT payload FROM learning_requests WHERE session_id=? AND result IS NULL ORDER BY rowid DESC LIMIT 1",
                        (session_id,),
                    ).fetchone()
                if row:
                    retry_request = json.loads(row["payload"])
            return {
                "session_id": session_id, "question": state.get("question", ""),
                "answer": state.get("answer", ""), "citations": state.get("citations", []),
                "answer_basis": state.get("answer_basis", ""),
                "goal_class": state.get("goal_class"), "evidence": state.get("evidence"),
                "concept_label": state.get("concept_label", ""),
                "pending": pending,
                "quiz": public_quiz(quiz, state["exercise_id"]) if quiz else None,
                "grading": state.get("grading"),
                "status": "cancelled" if session["cancelled"] else ("waiting" if pending else "ready"),
                "can_retry": can_retry, "retry_request": retry_request,
            }

    def cancel(self, session_id):
        """Mark the session cancelled immediately without waiting for the
        same-session operation lock. The in-flight operation observes the flag
        at its next node boundary and aborts; this returns right away."""
        with self._activity():
            with self.lock, self.connection:
                self._session(session_id)
                self.connection.execute("UPDATE learning_sessions SET cancelled=1 WHERE id=?", (session_id,))
        return self.view(session_id)

    def _session_cancelled(self, session_id) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT cancelled FROM learning_sessions WHERE id = ?", (session_id,),
            ).fetchone()
        return row is None or bool(row["cancelled"])

    def exercise(self, state, number, factory):
        key = f"{state['session_id']}:{state['task_id']}:{number}"
        exercise_id = hashlib.sha256(key.encode()).hexdigest()[:32]
        with self._activity():
            with self.lock:
                row = self.connection.execute("SELECT quiz FROM exercises WHERE id=?", (exercise_id,)).fetchone()
                if row:
                    quiz = json.loads(row["quiz"])
                    with self.connection:
                        self._record_exercise_metadata(exercise_id, state, quiz)
                    return exercise_id, quiz
            quiz = factory()
            if self._session_cancelled(state["session_id"]):
                raise LearningError("该会话已结束，请新建会话。", 409)
            with self.lock, self.connection:
                row = self.connection.execute("SELECT quiz FROM exercises WHERE id=?", (exercise_id,)).fetchone()
                if row:
                    quiz = json.loads(row["quiz"])
                else:
                    if self._session_cancelled(state["session_id"]):
                        raise LearningError("该会话已结束，请新建会话。", 409)
                    self.connection.execute(
                        "INSERT INTO exercises VALUES (?, ?, ?, ?, ?)",
                        (exercise_id, state["session_id"], state["task_id"], number, json.dumps(quiz, ensure_ascii=False)),
                    )
                self._record_exercise_metadata(exercise_id, state, quiz)
            return exercise_id, quiz

    def _concept(self, label, key, origin):
        normalized = unicodedata.normalize("NFC", " ".join(key.split())).casefold()
        concept_id = "concept_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        self.connection.execute(
            "INSERT OR IGNORE INTO learning_concepts VALUES (?, ?, ?, ?)",
            (concept_id, label, normalized, origin),
        )
        return concept_id

    def concept(self, question, label):
        with self.lock, self.connection:
            self._ensure_open()
            if label:
                return self._concept(label, "named:" + label, "named")
            return self._concept(question[:120], "topic:" + question, "topic")

    def _record_exercise_metadata(self, exercise_id, state, quiz):
        concept_id = state.get("concept_id")
        if not concept_id:
            key = f"legacy:{state['session_id']}:{state['task_id']}"
            concept_id = self._concept("历史练习 · " + quiz["question"][:80], key, "legacy")
        allowed = set(quiz["source_indices"])
        references = [item for item in to_citations(state.get("retrieved") or []) if item["index"] in allowed]
        self.connection.execute(
            "INSERT OR IGNORE INTO learning_exercise_metadata VALUES (?, ?, ?)",
            (exercise_id, concept_id, json.dumps(references, ensure_ascii=False)),
        )

    def record_teaching(self, state):
        with self.lock:
            self._ensure_open()
            if self._session_cancelled(state["session_id"]):
                raise LearningError("该会话已结束，请新建会话。", 409)
            concept_id = state.get("concept_id") or self.concept(state["question"], state.get("concept_label", ""))
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO learning_teaching_events VALUES (?, ?, ?, ?)",
                    (f"teach:{state['session_id']}:{state['task_id']}", concept_id,
                     json.dumps(state["citations"], ensure_ascii=False), utc_datetime(self.reviews.clock()).isoformat()),
                )
            return concept_id

    def progress(self):
        with self.lock:
            self._ensure_open()
            exercises = {}
            rows = self.connection.execute("""
                SELECT e.*, m.concept_id, m.source_refs, c.label, c.origin
                FROM exercises e
                LEFT JOIN learning_exercise_metadata m ON m.exercise_id=e.id
                LEFT JOIN learning_concepts c ON c.id=m.concept_id
                ORDER BY e.session_id, e.task_id, e.number
            """).fetchall()
            concepts = {}
            for row in rows:
                value = dict(row)
                if not value["concept_id"]:
                    key = f"legacy:{row['session_id']}:{row['task_id']}"
                    normalized = unicodedata.normalize("NFC", " ".join(key.split())).casefold()
                    value["concept_id"] = "concept_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
                    value["label"] = "历史练习 · " + json.loads(row["quiz"])["question"][:80]
                    value["origin"] = "legacy"
                value["source_refs"] = json.loads(value["source_refs"] or "[]")
                exercises[row["id"]] = value
                concepts.setdefault(value["concept_id"], {
                    "label": value["label"], "origin": value["origin"], "taught_count": 0,
                })
            taught = self.connection.execute("""
                SELECT c.id, c.label, c.origin, COUNT(*) AS taught_count
                FROM learning_teaching_events t JOIN learning_concepts c ON c.id=t.concept_id
                GROUP BY c.id
            """).fetchall()
            for row in taught:
                concepts[row["id"]] = {
                    "label": row["label"], "origin": row["origin"], "taught_count": row["taught_count"],
                }
            records = []
            unavailable_attempts = 0
            recorded_events = set()
            for row in self.connection.execute("SELECT * FROM review_events ORDER BY event_id").fetchall():
                exercise = exercises.get(row["item_id"])
                if exercise is None:
                    continue
                result = json.loads(row["payload"])["result"]
                response = json.loads(row["response"])
                records.append({
                    "attempt_id": row["event_id"], "concept_id": exercise["concept_id"],
                    "exercise_id": row["item_id"], "score": result["score"],
                    "max_score": result["max_score"],
                    "reviewed_at": datetime.fromisoformat(response["schedule"]["reviewed_at"]),
                    "source_refs": exercise["source_refs"],
                })
                recorded_events.add(row["event_id"])
            for row in self.connection.execute("SELECT * FROM attempts ORDER BY exercise_id").fetchall():
                if "initial:" + row["exercise_id"] in recorded_events:
                    continue
                exercise = exercises.get(row["exercise_id"])
                if exercise is None:
                    unavailable_attempts += 1
                    continue
                try:
                    result = json.loads(row["result"])
                    if not isinstance(result, dict):
                        raise ValueError("历史成绩须为字典")
                    score = result.get("score")
                    max_score = result.get("max_score", 1)
                    validate_review_score(score, max_score)
                except (ValueError, TypeError):
                    unavailable_attempts += 1
                    continue
                records.append({
                    "attempt_id": "legacy-attempt:" + row["exercise_id"],
                    "concept_id": exercise["concept_id"], "exercise_id": row["exercise_id"],
                    "score": score, "max_score": max_score, "reviewed_at": None,
                    "source_refs": exercise["source_refs"],
                })
            groups = {}
            for record in records:
                groups.setdefault(record["concept_id"], []).append(record)
            statistics = {item["concept_id"]: item for item in summarize_concept_progress(records)}
            results = []
            for concept_id, metadata in sorted(concepts.items(), key=lambda item: (item[1]["label"], item[0])):
                attempts = sorted(
                    groups.get(concept_id, []),
                    key=lambda item: (
                        item["reviewed_at"] or datetime.min.replace(tzinfo=timezone.utc), item["attempt_id"],
                    ),
                )
                timed = [item for item in attempts if item["reviewed_at"] is not None]
                results.append({
                    "concept_id": concept_id, **metadata,
                    **statistics.get(concept_id, summarize_learning([])),
                    "mastery": assess_concept_mastery(timed),
                    "undated_attempts": len(attempts) - len(timed),
                    "attempts": [
                        {**item, "reviewed_at": item["reviewed_at"].isoformat() if item["reviewed_at"] else None}
                        for item in attempts
                    ],
                })
            return {
                "summary": summarize_learning(records), "concepts": results,
                "unavailable_attempts": unavailable_attempts,
            }

    def grade(self, exercise_id, quiz, answer):
        with self.lock:
            self._ensure_open()
            row = self.connection.execute("SELECT * FROM attempts WHERE exercise_id=?", (exercise_id,)).fetchone()
            normalized = answer.strip().upper()
            if row:
                if row["answer"] != normalized:
                    raise LearningError("这道题已提交其他答案，请查看已有判分。")
                return json.loads(row["result"])
            session = self.connection.execute(
                "SELECT session_id FROM exercises WHERE id=?", (exercise_id,),
            ).fetchone()
            if session is not None and self._session_cancelled(session["session_id"]):
                raise LearningError("该会话已结束，请新建会话。", 409)
            result = grade_quiz(quiz, answer)
            with self.connection:
                self.connection.execute(
                    "INSERT INTO attempts VALUES (?, ?, ?)",
                    (exercise_id, normalized, json.dumps(result, ensure_ascii=False)),
                )
                self.reviews.record(
                    exercise_id, "initial:" + exercise_id, {**result, "max_score": 1},
                    title=quiz["question"], connection=self.connection,
                )
            return result

    def review_question(self, exercise_id):
        with self.lock:
            self._ensure_open()
            row = self.connection.execute("SELECT quiz FROM exercises WHERE id=?", (exercise_id,)).fetchone()
            if row is None:
                raise LearningError("复习题目不存在。", 404)
            return public_quiz(json.loads(row["quiz"]), exercise_id)

    def answer_review(self, exercise_id, request_id, answer, expected_review_at):
        with self.lock:
            self._ensure_open()
            row = self.connection.execute("SELECT quiz FROM exercises WHERE id=?", (exercise_id,)).fetchone()
            if row is None:
                raise LearningError("复习题目不存在。", 404)
            quiz = json.loads(row["quiz"])
            try:
                result = grade_quiz(quiz, answer)
                return self.reviews.record(
                    exercise_id, "review:" + request_id, {**result, "max_score": 1},
                    title=quiz["question"], expected_review_at=expected_review_at,
                )
            except ReviewConflict as error:
                raise LearningError(str(error)) from error
            except ValueError as error:
                raise LearningError(str(error), 422) from error

    @usage_request
    def events(self, session_id: str, request: dict):
        with self._activity(new_request=True), self._session_lock(session_id):
            with self.lock:
                if self._session(session_id)["cancelled"]:
                    raise LearningError("该会话已结束，请新建会话。")
            config = build_thread_config(session_id)
            snapshot = self.graph.get_state(config)
            request_id = request["request_id"]
            payload = json.dumps(request, ensure_ascii=False, sort_keys=True)
            with self.lock:
                row = self.connection.execute(
                    "SELECT * FROM learning_requests WHERE session_id=? AND request_id=?",
                    (session_id, request_id),
                ).fetchone()
            if row:
                previous_payload = json.loads(row["payload"])
                previous_payload.setdefault("concept_label", "")
                current_payload = {"concept_label": "", **request}
                if previous_payload != current_payload:
                    raise LearningError("同一请求编号的内容已改变，请恢复原请求或使用新编号。")
            if row and row["result"]:
                yield {"event": "done", "session": json.loads(row["result"])}
                return
            checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id") if snapshot.config else None
            if row and checkpoint_id != row["baseline"]:
                graph_input = None
            else:
                graph_input = self._input(session_id, request, snapshot)
            if row is None:
                with self.lock, self.connection:
                    self.connection.execute(
                        "INSERT INTO learning_requests(session_id,request_id,payload,baseline) VALUES (?,?,?,?)",
                        (session_id, request_id, payload, checkpoint_id),
                    )
            started = time.monotonic()
            request_started = started
            node_count = 0
            for update in self.graph.stream(graph_input, config, stream_mode="updates"):
                for node in update:
                    if node == "__interrupt__":
                        continue
                    now = time.monotonic()
                    step = {
                        "event": "step", "node": node, "status": "done",
                        "ms": max(0, int((now - started) * 1000)),
                        "request_id": request_id,
                    }
                    calls = drain_model_calls()
                    if calls:
                        step["model_calls"] = calls
                        step["tokens"] = sum_usage(calls)
                    yield step
                    node_count += 1
                    started = now
                    if self._session_cancelled(session_id):
                        raise LearningError("该会话已结束，请新建会话。", 409)
            if self._session_cancelled(session_id):
                raise LearningError("该会话已结束，请新建会话。", 409)
            result = self.view(session_id)
            with self.lock, self.connection:
                self.connection.execute(
                    "UPDATE learning_requests SET result=? WHERE session_id=? AND request_id=?",
                    (json.dumps(result, ensure_ascii=False), session_id, request_id),
                )
            LOGGER.info(
                "Learning operation done request_id=%s session=%s nodes=%d ms=%d model_usage=%s",
                request_id, session_id, node_count, max(0, int((time.monotonic() - request_started) * 1000)),
                json.dumps(request_usage(), ensure_ascii=False),
            )
            yield {"event": "done", "session": result}

    def _input(self, session_id, request, snapshot):
        pending = snapshot.interrupts[0].value if snapshot.interrupts else None
        if request["action"] == "start":
            if pending or snapshot.next:
                raise LearningError("当前任务尚未完成，请继续当前步骤或新建会话。")
            message = request["message"].strip()
            if not message:
                raise LearningError("请输入问题。", 422)
            return {
                "session_id": session_id, "task_id": request["request_id"], "question": message,
                "selected_mode": request.get("selected_mode"), "submode": request["submode"],
                "concept_label": request.get("concept_label", ""), "concept_id": "",
                "retrieved": [], "citations": [], "evidence": {}, "answer": "", "answer_basis": "",
                "quiz": None, "grading": None, "exercise_id": "", "exercise_number": 0,
                "user_answer": "", "next_action": "", "last_activity": "",
                "remediation_rounds": 0, "variant_rounds": 0,
            }
        if not pending or request.get("pending_id") != pending["pending_id"]:
            raise LearningError("当前步骤已变化，请刷新会话后重试。")
        if request["action"] not in pending["actions"]:
            raise LearningError("当前步骤不支持该操作。")
        if request["action"] == "answer":
            answer = request.get("answer")
            if not isinstance(answer, str) or answer.strip().upper() not in ("A", "B", "C", "D"):
                raise LearningError("请选择 A、B、C、D 中的一个答案。", 422)
        return Command(resume={"action": request["action"], "answer": request.get("answer", "")})
