"""Resolve a local evidence gap with model knowledge or one public web lookup."""
from app.graph.citations import extract_source_numbers
from app.graph.nodes.evidence import assess_evidence
from app.graph.nodes.fallback import plan_fallback, web_search_forbidden
from app.graph.nodes.teach import generate_teaching, search_external_evidence
from app.rag.retriever import to_citations
from app.tools.web_search import WebSearchError, search_web
from app.llm import chat


def resolve_knowledge_gap(question: str, *, require_sources: bool = False, submode: str = "explain") -> dict:
    plan = plan_fallback(question)
    empty = {"retrieved": [], "citations": [], "answer_basis": "none"}
    if plan["decision"] == "clarify":
        return {**empty, "answer": plan["answer"]}
    if plan["decision"] == "answer" and not require_sources:
        answer = plan['answer']
        if not answer:
            answer = chat([
                {'role': 'system', 'content': (
                    '根据你有把握的稳定公开知识、基础概念和简单推理回答用户问题。'
                    '用清楚的中文说明必要条件和不确定性，保留计算及推理所需步骤。'
                    '本轮依据为模型知识，引用的材料以当前实际提供内容为准。'
                    '涉及缺少上下文的私有项目时，明确说明需要补充的资料。'
                )},
                {'role': 'user', 'content': question},
            ])
        return {**empty, "answer": answer, "answer_basis": "model"}

    query = plan["search_query"] or question
    if web_search_forbidden(question):
        return {**empty, "answer": "本次已按要求禁止联网。知识库资料不足，请补充相关资料后重试。"}
    try:
        chunks = search_external_evidence(query, ["参考资料"], search_web)
    except WebSearchError as error:
        messages = {
            "timeout": "联网搜索超时，请稍后重试。",
            "authentication": "搜索服务鉴权失败，请检查服务配置。",
            "rate_limit": "搜索服务额度或请求频率受限，请稍后重试。",
            "configuration": "搜索服务配置无效，请检查配置。",
            "forbidden": "本次请求已禁止联网搜索。",
        }
        detail = messages.get(error.code, "联网搜索暂时不可用。请稍后重试，或补充相关资料。")
        return {**empty, "answer": "知识库资料不足，" + detail}
    if not chunks:
        return {**empty, "answer": "知识库资料不足，联网搜索也未找到可用资料。请补充问题条件或相关文档。"}
    evidence = assess_evidence(question, chunks)
    if evidence["status"] == "insufficient":
        return {**empty, "answer": "联网结果仍未覆盖这个问题，请补充更具体的条件或相关资料。"}

    selected = set(evidence["supporting_indices"])
    citations = [item for item in to_citations(chunks) if item["index"] in selected]
    answer = ""
    if not require_sources:
        answer = generate_teaching(question, chunks, evidence, submode)
        numbers = extract_source_numbers(answer)
        if not numbers or not numbers.issubset(selected):
            raise ValueError("联网回答缺少有效来源编号，请重试生成。")
    return {
        "answer": answer, "answer_basis": "web", "retrieved": chunks,
        "citations": citations, "evidence": evidence,
    }
