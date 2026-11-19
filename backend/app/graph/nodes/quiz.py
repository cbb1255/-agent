"""Evidence-grounded single-choice question generation exercise."""
import json

from app.graph.citations import extract_source_numbers
from app.graph.grounding import require_answer_key_support
from app.graph import grounding
from app.graph.nodes.teach import build_teaching_context
from app.llm import chat
from app.prompts import QUIZ_SYSTEM


def generate_quiz(question: str, chunks: list[dict], evidence: dict) -> dict:
    """返回测验，其中包含选项、正确答案和证据。

    证据是对同一问题和组块的经过验证的评估。
    拒绝空白问题或证据不足。仅使用支持的
    源，保留原始编号，并生成一个 JSON 测验。
    验证非空文本、四个不同的选项、答案标签、
    以及从supporting_indices 中提取的唯一整数源索引。
    解析必须能够支撑 correct_answer，失败时纠正一次，
    仍失败则拒绝入库。API 适配器将仅向学习者公开适当的字段。
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题必须是非空字符串")
    question = question.strip()
    if not isinstance(evidence, dict):
        raise ValueError("evidence 必须是证据判断字典")
    status = evidence.get("status")
    if status not in ("sufficient", "partial", "insufficient"):
        raise ValueError("证据 status 无效")
    if status == "insufficient":
        raise ValueError("证据不足，无法生成有依据的题目")
    allowed_indices = evidence.get("supporting_indices")
    context = build_teaching_context(chunks, allowed_indices)
    if not allowed_indices:
        raise ValueError("出题至少需要一条支撑资料")
    messages = [
        {"role": "system", "content": QUIZ_SYSTEM.format(context=context)},
        {"role": "user", "content": question},
    ]
    for attempt in range(2):
        response = chat(messages, json_mode=True)
        try:
            candidate = validate_quiz_response(response, allowed_indices)
            require_answer_key_support(
                candidate["explanation"], candidate["options"], candidate["correct_answer"],
            )
            grounding.review_quiz(candidate, chunks, candidate["source_indices"])
        except ValueError as error:
            if attempt == 1:
                raise
            correction = (
                f"输出校验未通过，原因是 {error}。"
                "请确保解析明确支撑 correct_answer 选项的内容，"
                "并重新返回约定格式的 JSON。"
            )
        else:
            return candidate
        messages.extend([
            {"role": "assistant", "content": response},
            {"role": "user", "content": correction},
        ])


def validate_quiz_response(response: str, allowed_indices: list[int]) -> dict:
    """Validate the shared single-choice output schema and source boundary."""
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("模型结果须为 JSON 对象")
    exam_question = payload.get("question")
    if not isinstance(exam_question, str) or not exam_question.strip():
        raise ValueError("题干必须是非空字符串")
    exam_question = exam_question.strip()
    options = payload.get("options")
    if not isinstance(options, dict):
        raise ValueError("options 必须是字典")
    if set(options) != {"A", "B", "C", "D"}:
        raise ValueError("options 必须恰好包含 A、B、C、D")
    cleaned_options = {}
    for label, text in options.items():
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"选项 {label} 必须是非空字符串")
        cleaned_options[label] = text.strip()
    if len(set(cleaned_options.values())) != 4:
        raise ValueError("四个选项的内容不能重复")
    explanation = payload.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("解析必须是非空字符串")
    explanation = explanation.strip()
    source_indices = payload.get("source_indices")
    if not isinstance(source_indices, list) or not source_indices:
        raise ValueError("source_indices 必须是非空列表")
    for index in source_indices:
        if type(index) is not int:
            raise ValueError("来源编号必须是整数，不能是布尔值")
        if index not in allowed_indices:
            raise ValueError("来源编号必须属于证据允许的支撑编号")
    if len(set(source_indices)) != len(source_indices):
        raise ValueError("来源编号不能重复")
    explanation_indices = extract_source_numbers(explanation)
    if not explanation_indices.issubset(set(source_indices)):
        raise ValueError("解析中的来源编号必须属于题目的 source_indices")
    correct_answer = payload.get("correct_answer")
    if not isinstance(correct_answer, str):
        raise ValueError("correct_answer 必须是字符串")
    correct_answer = correct_answer.strip()
    if correct_answer not in cleaned_options:
        raise ValueError("correct_answer 必须是 A、B、C、D 中的一个标签")
    return {
        "question": exam_question,
        "options": cleaned_options,
        "correct_answer": correct_answer,
        "explanation": explanation,
        "source_indices": source_indices,
    }
# {
#     "question": "RRF 主要用于什么？",
#     "options": {
#         "A": "融合多个检索结果的排序",
#         "B": "生成文档向量",
#         "C": "拆分 Markdown 文档",
#         "D": "保存会话检查点",
#     },
#     "correct_answer": "A",
#     "explanation": "资料说明 RRF 根据各路结果的排名进行融合。",
#     "source_indices": [2],
# }
