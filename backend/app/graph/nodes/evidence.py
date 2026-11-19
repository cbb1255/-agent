"""Evidence sufficiency classification exercise."""
import json

from app.llm import chat
from app.prompts import EVIDENCE_CHECK_SYSTEM, build_context


def assess_evidence(question: str, chunks: list[dict], *, max_sources: int | None = None) -> dict:
    """检查覆盖范围和退货状态、支持索引、缺失点、原因。
    拒绝空白问题。空块列表返回不足，没有
    调用模型。否则使用编号上下文和 JSON 模型响应，
    然后验证输出形状、引文索引和状态一致性。
    保留调用者的块和源编号。传播模型错误

用户问“RRF 的原理和局限是什么”，资料只讲了原理，就只能判断为部分充分。即使检索分数很高，也需要检查正文是否覆盖问题。

判断检索资料对问题的覆盖程度，并返回支撑来源与缺失要点。函数骨架和提示词已备好，核心实现留给你。
    """
    if not question.strip():
        raise ValueError
    if max_sources is not None and (type(max_sources) is not int or max_sources <= 0):
        raise ValueError("max_sources须为正整数")
    if not chunks:
        return  {"status":"insufficient", "supporting_indices":[],
            "missing_points":["缺少回答该问题所需的相关资料"],
            "reason": "没有检索到参考资料"}
    context=build_context(chunks)
    content = (
    f"用户问题\n{question.strip()}\n\n"
    f"参考资料\n{context}")
    system = EVIDENCE_CHECK_SYSTEM
    if max_sources is not None:
        system += (
            f"\n从候选中挑选支撑原问题各要点的必要资料，supporting_indices最多{max_sources}条。"
            "按对原问题的必要性与覆盖范围排序，优先覆盖定义、操作步骤和必要条件，选择内容互补的片段。"
            "每条编号保持输入中的原编号。"
        )
    response=chat([{"role": "system", "content": system},
                {"role": "user", "content":content}
            ], json_mode=True)
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("模型结果须为 JSON 对象")
    reason = payload.get("reason")
    if not isinstance(reason, str):
        raise ValueError("reason 必须是字符串")
    reason = reason.strip()
    if not reason:
        raise ValueError("reason 不能为空")
    missing_points = payload.get("missing_points")
    if not isinstance(missing_points, list):
        raise ValueError("missing_points 必须是列表")

    for point in missing_points:
        if not isinstance(point, str):
            raise ValueError("缺失要点必须是字符串")
        if not point.strip():
            raise ValueError("缺失要点不能为空字符串")
    if not isinstance(payload, dict):
        raise ValueError("模型分类结果须为 JSON 对象。")
    allowed_status=["sufficient","partial","insufficient"]
    status = payload.get("status")
    supporting_indices = payload.get("supporting_indices")
    has_support = bool(supporting_indices)
    has_missing = bool(missing_points)
    if status == "sufficient":
        if not has_support or has_missing:
            raise ValueError("sufficient 要求有支撑，且没有缺失要点")
    elif status == "partial":
        if not has_support or not has_missing:
            raise ValueError("partial 要求有支撑，也有缺失要点")
    elif status == "insufficient":
        if has_support or not has_missing:
            raise ValueError("insufficient 要求没有支撑，但有缺失要点")
    if payload.get("status") not in allowed_status:
        raise ValueError("status 无效")
    if not payload.get("reason").strip():
        raise ValueError("reason 为空")
    if (not isinstance(supporting_indices, list)or not all(
        type(index) is int and 1 <= index <= len(chunks)for index in payload.get("supporting_indices"))or len(payload.get("supporting_indices")) != len(set(payload.get("supporting_indices")))):
        raise ValueError(
            "supporting_indices 必须是无重复的整数列表，且编号在 1 到 len(chunks) 之间"
        )
    if max_sources is not None and len(supporting_indices) > max_sources:
        raise ValueError("支撑来源数量超过max_sources")
    return {
    "status": status,
    "supporting_indices": supporting_indices,
    "missing_points": missing_points,
    "reason": reason,
}
# {
#     "status": "partial",              # 充分、部分充分或不足
#     "supporting_indices": [1],        # 哪些片段提供了依据
#     "missing_points": ["缺少局限性"],  # 还缺什么
#     "reason": "资料只覆盖了原理",      # 为什么这样判断
# }
