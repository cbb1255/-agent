"""Targeted remediation generation exercise."""
from app.graph.nodes.teach import build_teaching_context
from app.llm import chat
from app.prompts import REMEDIATION_SYSTEM


def generate_remediation(quiz: dict, grading: dict, chunks: list[dict]) -> str:
    """使用测验的固定源片段解释错误答案。
    测验和评分是同一问题的经过验证的输出。一个正确的
    答案返回固定的完成文本而不调用模型。
    对于错误答案，请选择原始测验来源并提供两者
    REMEDIATION_SYSTEM 的选项标签和文本、说明和上下文。
    返回非空的剥离模型文本并保留所有调用者输入
    """
    if not isinstance(quiz, dict):
        raise ValueError("quiz 必须是题目字典")
    if not isinstance(grading, dict):
        raise ValueError("grading 必须是判分结果字典")

    user_answer = grading.get("user_answer")
    graded_correct_answer = grading.get("correct_answer")
    correct_answer = quiz.get("correct_answer")
    for field, label in (
        ("grading.user_answer", user_answer),
        ("grading.correct_answer", graded_correct_answer),
        ("quiz.correct_answer", correct_answer),
    ):
        if not isinstance(label, str) or label not in ("A", "B", "C", "D"):
            raise ValueError(f"{field} 必须是 A、B、C、D 中的一个标签")
    if correct_answer != graded_correct_answer:
        raise ValueError("题目与判分结果中的 correct_answer 必须一致")
    if user_answer == correct_answer:
        return "本题已答对，可以继续下一题。"

    question = quiz.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("题干必须是非空字符串")
    explanation = quiz.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("原题解析必须是非空字符串")
    options = quiz.get("options")
    if not isinstance(options, dict):
        raise ValueError("options 必须是选项字典")
    for label in (user_answer, correct_answer):
        text = options.get(label)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"选项 {label} 必须存在且包含非空正文")
    source_indices = quiz.get("source_indices")
    if not isinstance(source_indices, list) or not source_indices:
        raise ValueError("source_indices 必须是非空列表")

    context = build_teaching_context(chunks, source_indices)
    content = REMEDIATION_SYSTEM.format(
        question=question.strip(),
        selected_option=f"{user_answer}. {options[user_answer].strip()}",
        correct_option=f"{correct_answer}. {options[correct_answer].strip()}",
        explanation=explanation.strip(),
        context=context,
    )
    response = chat([
        {"role": "system", "content": content},
        {"role": "user", "content": question.strip()},
    ])
    if not isinstance(response, str) or not response.strip():
        raise RuntimeError("模型没有返回有效的补讲正文")
    return response.strip()
