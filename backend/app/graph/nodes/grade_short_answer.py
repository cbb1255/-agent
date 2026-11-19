"""Model-scored short answers with validated percentage scores and feedback."""
import json

from app.llm import chat
from app.prompts import SHORT_ANSWER_GRADING_SYSTEM


def grade_short_answer(quiz: dict, user_answer: str) -> dict:
    """让模型整体评分，校验百分制分数与反馈，并保留原题来源。"""
    if not isinstance(quiz, dict):
        raise ValueError("quiz 必须是题目字典")
    if not isinstance(user_answer, str) or not user_answer.strip():
        raise ValueError("用户回答必须是非空字符串")
    question = quiz.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("题干必须是非空字符串")
    reference_answer = quiz.get("reference_answer")
    if not isinstance(reference_answer, str) or not reference_answer.strip():
        raise ValueError("参考答案必须是非空字符串")
    source_indices = quiz.get("source_indices")
    if not isinstance(source_indices, list) or not source_indices:
        raise ValueError("source_indices 必须是非空列表")
    for index in source_indices:
        if type(index) is not int or index < 1:
            raise ValueError("来源编号必须是正整数，不能是布尔值")
    if len(set(source_indices)) != len(source_indices):
        raise ValueError("来源编号不能重复")

    content = SHORT_ANSWER_GRADING_SYSTEM.format(
        question=question.strip(),
        reference_answer=reference_answer.strip(),
    )
    response = chat([
        {"role": "system", "content": content},
        {"role": "user", "content": user_answer.strip()},
    ], json_mode=True)
    if not isinstance(response, str):
        raise ValueError("模型结果必须是 JSON 字符串")
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("模型结果须为 JSON 对象")
    score = payload.get("score")
    strengths = payload.get("strengths")
    missing_points = payload.get("missing_points")
    feedback = payload.get("feedback")
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("score 必须是 0 到 100 的整数")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError("feedback 必须是非空字符串")
    for field, points in (("strengths", strengths), ("missing_points", missing_points)):
        if not isinstance(points, list):
            raise ValueError(f"{field} 必须是列表")
        for point in points:
            if not isinstance(point, str) or not point.strip():
                raise ValueError(f"{field} 中每项必须是非空字符串")
    return {
        "score": score,
        "feedback": feedback.strip(),
        "strengths": [point.strip() for point in strengths],
        "missing_points": [point.strip() for point in missing_points],
        "max_score": 100,
        "source_indices": list(source_indices),
    }
