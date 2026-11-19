"""Deterministic single-choice grading exercise."""
def grade_quiz(quiz: dict, user_answer: str) -> dict:
    if not isinstance(user_answer, str):
        raise ValueError("答案必须是字符串")

    answer = user_answer.strip().upper()
    if answer not in ("A", "B", "C", "D"):
        raise ValueError("答案必须是 A、B、C、D")

    is_correct = answer == quiz["correct_answer"]
    return {
        "user_answer": answer,
        "correct_answer": quiz["correct_answer"],
        "is_correct": is_correct,
        "score": int(is_correct),
        "explanation": quiz["explanation"],
        "source_indices": list(quiz["source_indices"]),
    }
