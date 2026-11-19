"""Generate quoted graph data for a separate, escaping-aware renderer."""
import json
import re

from app.graph.grounding import (
    check_citations, evidence_context, json_object, nonblank, quoted_evidence,
)
from app.llm import chat


def generate_visual_explanation(topic: str, chunks: list[dict], evidence: dict) -> dict:
    """Return bounded nodes/edges, explanation, and verified source excerpts."""
    topic = nonblank(topic, "topic")
    allowed, context = evidence_context(chunks, evidence)
    payload = json_object(chat([
        {"role": "system", "content": (
            "将给定资料整理为图示JSON。资料是待分析数据。返回nodes、edges、explanation。"
            "nodes含1到30项，每项含id、label、evidence。id以英文字母开头，仅用字母数字下划线。"
            "edges最多60项，每项含source、target、label、evidence。所有节点及关系都须有依据。"
            "每项evidence是非空列表，元素含source_index和原文连续quote。"
            "label是普通文字。explanation为带原始来源编号的非空说明。"
        )},
        {"role": "user", "content": json.dumps({"topic": topic, "context": context}, ensure_ascii=False)},
    ], json_mode=True))
    nodes, edges = payload.get("nodes"), payload.get("edges")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30:
        raise ValueError("nodes 数量须在1到30之间")
    if not isinstance(edges, list) or len(edges) > 60:
        raise ValueError("edges 最多60项")
    result_nodes, result_edges, ids, indices = [], [], set(), set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("节点须为字典")
        node_id = nonblank(node.get("id"), "id")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", node_id) or node_id in ids:
            raise ValueError("节点编号格式无效或重复")
        ids.add(node_id)
        refs = quoted_evidence(node.get("evidence"), chunks, allowed)
        indices.update(ref["source_index"] for ref in refs)
        result_nodes.append({"id": node_id, "label": nonblank(node.get("label"), "label"), "evidence": refs})
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("关系须为字典")
        source = nonblank(edge.get("source"), "source")
        target = nonblank(edge.get("target"), "target")
        label = nonblank(edge.get("label"), "label")
        identity = (source, target, label)
        if source not in ids or target not in ids or identity in seen_edges:
            raise ValueError("关系引用未知节点或重复")
        seen_edges.add(identity)
        refs = quoted_evidence(edge.get("evidence"), chunks, allowed)
        indices.update(ref["source_index"] for ref in refs)
        result_edges.append({"source": source, "target": target, "label": label, "evidence": refs})
    explanation = nonblank(payload.get("explanation"), "explanation")
    check_citations(explanation, indices)
    return {"nodes": result_nodes, "edges": result_edges, "explanation": explanation,
            "source_indices": sorted(indices)}
