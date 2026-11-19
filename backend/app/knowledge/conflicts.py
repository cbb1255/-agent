"""Grounded pairwise conflict assessment exercise for governance reports."""
import json

from app.llm import chat
from app.prompts import DOCUMENT_CONFLICT_SYSTEM


def assess_document_conflict(
    first_document: dict, second_document: dict,
) -> dict:
    """评估两份文档的主张，返回状态、理由及经原文校验的证据。

    输入须包含非空白字符串 doc_id、revision_id 和 text，文档编号须不同。
    理由及摘录去掉首尾空白，冲突证据须连续出现在对应原文中。
    compatible 和 uncertain 返回空证据列表。非法输入和模型输出抛出
    ValueError，模型连接异常传播给调用方。输入文档保持完整。
    """
    for document in (first_document, second_document):
        if not isinstance(document, dict):
            raise ValueError("每份文档必须是字典")
        for field in ("doc_id", "revision_id", "text"):
            value = document.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 必须是非空白字符串")
    if first_document["doc_id"] == second_document["doc_id"]:
        raise ValueError("两份文档的 doc_id 必须不同")
    content = json.dumps(
        {
            "first_document": {
                "doc_id": first_document["doc_id"],
                "revision_id": first_document["revision_id"],
                "text": first_document["text"],
            },
            "second_document": {
                "doc_id": second_document["doc_id"],
                "revision_id": second_document["revision_id"],
                "text": second_document["text"],
            },
        },
        ensure_ascii=False,
    )
    response = chat([
        {"role": "system", "content": DOCUMENT_CONFLICT_SYSTEM},
        {"role": "user", "content": content},
    ], json_mode=True)
    if not isinstance(response, str):
        raise ValueError("模型结果须为 JSON 字符串")
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("模型结果须为 JSON 对象")
    status = payload.get("status")
    if status not in ("conflict", "compatible", "uncertain"):
        raise ValueError("模型返回的 status 无效")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason 必须是非空白字符串")
    first_quote = payload.get("first_quote")
    second_quote = payload.get("second_quote")
    if not isinstance(first_quote, str) or not isinstance(second_quote, str):
        raise ValueError("两个摘录字段必须是字符串")
    first_quote = first_quote.strip()
    second_quote = second_quote.strip()

    evidence = []
    if status == "conflict":
        if not first_quote or first_quote not in first_document["text"]:
            raise ValueError("第一段摘录必须非空，且连续出现在第一份原文中")
        if not second_quote or second_quote not in second_document["text"]:
            raise ValueError("第二段摘录必须非空，且连续出现在第二份原文中")
        evidence = [
            {
                "doc_id": first_document["doc_id"],
                "revision_id": first_document["revision_id"],
                "quote": first_quote,
            },
            {
                "doc_id": second_document["doc_id"],
                "revision_id": second_document["revision_id"],
                "quote": second_quote,
            },
        ]
    elif first_quote or second_quote:
        raise ValueError("compatible 和 uncertain 的摘录必须为空")
    return {
        "status": status,
        "reason": reason.strip(),
        "evidence": evidence,
    }
