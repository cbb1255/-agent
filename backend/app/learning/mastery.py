"""Evidence-based concept status exercise."""
from datetime import timedelta

from app.learning.review import utc_datetime, validate_review_score

def assess_concept_mastery(attempts: list[dict]) -> dict:
    """根据同一知识点最近三次独立答题的成绩和时间判断学习状态。

    按规范化编号去重，同一编号的分数、满分和 UTC 时刻须一致。
    按 UTC 时间和编号排序，保留输入。达到 80% 视为通过，最近
    三次均通过且首尾间隔至少 24 小时判为 stable。返回判断状态、
    最新百分制成绩、证据编号和理由。百分数只在展示时四舍五入。
    """
    if not isinstance(attempts, list):
        raise ValueError("attempts 必须是列表")
    if not attempts:
        return {
            "status": "unassessed",
            "latest_percentage": None,
            "recent_attempt_ids": [],
            "reason": "暂无答题记录。",}
    seen = {}
    normalized_records = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("每条答题记录必须是字典")
        score = attempt.get("score")
        max_score = attempt.get("max_score")
        validate_review_score(score, max_score)
        reviewed_at = utc_datetime(attempt.get("reviewed_at"))
        attempt_id = attempt.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id 必须是非空字符串")
        attempt_id = attempt_id.strip()
        current = (score, max_score, reviewed_at)
        if attempt_id in seen:
            if seen[attempt_id] != current:
                raise ValueError("同一答题编号的成绩、满分或作答时刻冲突")
            continue
        seen[attempt_id] = current
        normalized_records.append({
            "attempt_id": attempt_id,
            "score": score,
            "max_score": max_score,
            "reviewed_at": reviewed_at,
        })
    ordered = sorted(
        normalized_records,
        key=lambda attempt: (attempt["reviewed_at"], attempt["attempt_id"]),)
    recent = ordered[-3:]
    passed = [
        item["score"] * 5 >= item["max_score"] * 4
        for item in recent
    ]
    if not passed[-1]:
        status = "needs_review"
    elif (
        len(passed) == 3
        and all(passed)
        and recent[-1]["reviewed_at"] - recent[0]["reviewed_at"] >= timedelta(hours=24)
    ):
        status = "stable"
    else:
        status = "practicing"
    latest = recent[-1]
    latest_percentage = round(latest["score"] / latest["max_score"] * 100, 2)
    reasons = {
        "needs_review": "最近一次成绩低于80%，需要复习。",
        "stable": "最近三次均达到80%，且首尾间隔至少24小时。",
        "practicing": "最近一次已通过，仍需积累间隔复测记录。",
    }
    return {
        "status": status,
        "latest_percentage": latest_percentage,
        "recent_attempt_ids": [item["attempt_id"] for item in recent],
        "reason": reasons[status],
    }
