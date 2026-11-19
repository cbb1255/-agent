"""Generate a grounded variant quiz with one output-correction attempt."""
from app.graph.grounding import require_answer_key_support
from app.graph import grounding
from app.graph.nodes.quiz import validate_quiz_response
from app.graph.nodes.teach import build_teaching_context
from app.llm import chat
from app.prompts import VARIANT_QUIZ_SYSTEM


def generate_variant_quiz(
    previous_quiz: dict,
    chunks: list[dict],
    missing_points: list[str],
) -> dict:
    """Preserve sources and correct a malformed or duplicate output once."""
    if not isinstance(previous_quiz, dict):
        raise ValueError("previous_quiz 必须是原题字典")
    question = previous_quiz.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("原题题干必须是非空字符串")
    question = question.strip()
    source_indices = previous_quiz.get("source_indices")
    if not isinstance(source_indices, list) or not source_indices:
        raise ValueError("原题 source_indices 必须是非空列表")
    if not isinstance(missing_points, list):
        raise ValueError("missing_points 必须是列表")
    for point in missing_points:
        if not isinstance(point, str) or not point.strip():
            raise ValueError("缺失要点必须是非空字符串")

    missing_text = "\n".join(point.strip() for point in missing_points)
    if not missing_text:
        missing_text = "巩固原题涉及的知识点"
    context = build_teaching_context(chunks, source_indices)
    messages = [
        {"role": "system", "content": VARIANT_QUIZ_SYSTEM.format(
            previous_question=question,
            missing_points=missing_text,
            context=context,
        )},
        {"role": "user", "content": question},
    ]
    old_normalized = "".join(question.split()).casefold()
    for attempt in range(2):
        response = chat(messages, json_mode=True)
        try:
            candidate = validate_quiz_response(response, source_indices)
            require_answer_key_support(
                candidate["explanation"], candidate["options"], candidate["correct_answer"],
            )
            grounding.review_quiz(candidate, chunks, candidate["source_indices"])
        except ValueError as error:
            if attempt == 1:
                raise
            correction = f"输出校验未通过，原因是 {error}。"
        else:
            if "".join(candidate["question"].split()).casefold() != old_normalized:
                return candidate
            if attempt == 1:
                raise RuntimeError("重试后题干仍与原题重复，请稍后重试")
            correction = f"刚才生成的题干是：{candidate['question']}，它与原题重复。请更换提问角度或应用情境。"
        messages.extend([
            {"role": "assistant", "content": response},
            {"role": "user", "content": correction +
             "保持原知识点和资料范围，重新返回约定格式的 JSON。options 必须是 A、B、C、D 为键、选项字符串为值的对象。"},
        ])
