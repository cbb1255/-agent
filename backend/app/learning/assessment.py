"""Finite, deterministic assessment plans and evidence-preserving reports."""
from copy import deepcopy
from datetime import datetime, timezone

from app.graph.grounding import nonblank, positive_integer, string_list
from app.learning.progress import summarize_learning
from app.learning.review import utc_datetime


def _records(records):
    if not isinstance(records, list):
        raise ValueError("学习记录必须是列表")
    checked = []
    identities = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("每条学习记录须为字典")
        attempt_id = nonblank(record.get("attempt_id"), "attempt_id")
        goal = nonblank(record.get("goal"), "goal")
        points = string_list(record.get("missing_points", []), "missing_points")
        sources = record.get("source_refs", [])
        if not isinstance(sources, list) or any(not isinstance(ref, dict) for ref in sources):
            raise ValueError("source_refs 必须是字典列表")
        due_at = record.get("due_at")
        if due_at is not None:
            if isinstance(due_at, str):
                try:
                    due_at = datetime.fromisoformat(due_at)
                except ValueError as error:
                    raise ValueError("due_at 必须是带时区的时间") from error
            due_at = utc_datetime(due_at)
        item = deepcopy(record) | {"attempt_id": attempt_id, "goal": goal, "missing_points": points,
                                   "source_refs": deepcopy(sources), "due_at": due_at}
        summarize_learning([item])
        if attempt_id in identities:
            if identities[attempt_id] != item:
                raise ValueError("同一答题编号的记录冲突")
            continue
        identities[attempt_id] = item
        checked.append(item)
    return checked


def plan_assessment(
    mode: str, goals: list[str], learning_records: list[dict] | None = None,
    *, max_questions: int = 6, now: datetime | None = None,
) -> dict:
    """Plan diagnose/interview/review dimensions from explicit learning goals.

    Records contain attempt_id, goal, score, max_score and optional missing_points,
    source_refs and aware due_at. Exact retry records are deduplicated. Goal order
    is stable; review puts due goals first, then goals with the weakest history.
    """
    if mode not in ("diagnose", "interview", "review"):
        raise ValueError("mode 须为diagnose、interview或review")
    positive_integer(max_questions, "max_questions")
    normalized_goals = string_list(goals, "goals")
    records = _records([] if learning_records is None else learning_records)
    current_time = utc_datetime(now if now is not None else datetime.now(timezone.utc))
    dimensions = []
    for index, goal in enumerate(normalized_goals, 1):
        history = [record for record in records if record["goal"] == goal]
        summary = summarize_learning(history)
        score = summary["average_score"] if history else None
        due = any(record["due_at"] is not None and record["due_at"] <= current_time for record in history)
        difficulty = "medium" if mode == "interview" else "easy"
        if score is not None:
            difficulty = "hard" if score >= 80 else "medium" if score >= 50 else "easy"
        dimensions.append({"dimension_id": f"goal-{index}", "goal": goal, "difficulty": difficulty,
                           "historical_score": score, "due": due})
    if mode == "review":
        dimensions.sort(key=lambda item: (not item["due"], item["historical_score"] if item["historical_score"] is not None else -1))
    return {"mode": mode, "dimensions": dimensions, "max_questions": max_questions,
            "max_per_dimension": 2, "pass_score": 80, "created_at": current_time.isoformat()}


def _assessment_input(plan, results):
    if not isinstance(plan, dict) or plan.get("mode") not in ("diagnose", "interview", "review"):
        raise ValueError("评估计划无效")
    positive_integer(plan.get("max_questions"), "max_questions")
    positive_integer(plan.get("max_per_dimension"), "max_per_dimension")
    if type(plan.get("pass_score")) is not int or not 0 <= plan["pass_score"] <= 100:
        raise ValueError("pass_score 必须是百分制整数")
    dimensions = plan.get("dimensions")
    if not isinstance(dimensions, list):
        raise ValueError("dimensions 必须是列表")
    ids, goals = set(), set()
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise ValueError("dimension 必须是字典")
        dimension_id = nonblank(dimension.get("dimension_id"), "dimension_id")
        goal = nonblank(dimension.get("goal"), "goal")
        if dimension_id in ids or goal in goals or dimension.get("difficulty") not in ("easy", "medium", "hard"):
            raise ValueError("维度重复或难度无效")
        ids.add(dimension_id)
        goals.add(goal)
    records = _records(results)
    for record in records:
        if record["goal"] not in goals:
            raise ValueError("答题记录不属于本次评估目标")
    return dimensions, records


def select_next_assessment_question(plan: dict, results: list[dict]) -> dict:
    """Return one generation request or a stable end decision, without model I/O."""
    dimensions, records = _assessment_input(plan, results)
    if len(records) >= plan["max_questions"]:
        return {"action": "end", "reason": "已达到本次评估题数上限。"}
    unasked, followups = [], []
    for dimension in dimensions:
        attempts = [record for record in records if record["goal"] == dimension["goal"]]
        if not attempts:
            unasked.append((dimension, attempts))
        elif len(attempts) < plan["max_per_dimension"]:
            latest = attempts[-1]
            if latest["score"] * 100 < latest["max_score"] * plan["pass_score"] or latest["missing_points"]:
                followups.append((dimension, attempts))
    candidates = followups + unasked if plan["mode"] == "interview" else unasked + followups
    if not candidates:
        return {"action": "end", "reason": "各维度已完成首轮考察及允许的定向追问。"}
    dimension, attempts = candidates[0]
    return {"action": "generate", "reason": "针对薄弱点追问。" if attempts else "开始该维度的首轮考察。",
            "question_id": f"{dimension['dimension_id']}-q{len(attempts) + 1}",
            "dimension_id": dimension["dimension_id"], "goal": dimension["goal"],
            "difficulty": dimension["difficulty"], "mode": plan["mode"],
            "focus_points": list(attempts[-1]["missing_points"]) if attempts else []}


def summarize_assessment(plan: dict, results: list[dict]) -> dict:
    """Report observed scores, unassessed goals and evidence for each dimension."""
    dimensions, records = _assessment_input(plan, results)
    report = []
    for dimension in dimensions:
        attempts = [record for record in records if record["goal"] == dimension["goal"]]
        summary = summarize_learning(attempts)
        status = "not_assessed"
        if attempts:
            latest = attempts[-1]
            passed = latest["score"] * 100 >= latest["max_score"] * plan["pass_score"]
            status = "understood" if passed and not latest["missing_points"] else "needs_review"
        report.append({"dimension_id": dimension["dimension_id"], "goal": dimension["goal"],
                       "status": status, "summary": summary,
                       "missing_points": list(attempts[-1]["missing_points"]) if attempts else [],
                       "evidence": [{"attempt_id": item["attempt_id"], "score": item["score"],
                                     "max_score": item["max_score"], "source_refs": deepcopy(item["source_refs"])} for item in attempts]})
    next_step = select_next_assessment_question(plan, results)
    return {"mode": plan["mode"], "completed": next_step["action"] == "end", "stop_reason": next_step["reason"],
            "summary": summarize_learning(records), "dimensions": report,
            "weak_goals": [item["goal"] for item in report if item["status"] == "needs_review"],
            "unassessed_goals": [item["goal"] for item in report if item["status"] == "not_assessed"],
            "recommendations": [f"复习并重新考察{item['goal']}。" for item in report if item["status"] == "needs_review"]}
