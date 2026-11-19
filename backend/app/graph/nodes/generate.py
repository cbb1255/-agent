"""Answer from retrieved evidence or return the empty-evidence response."""
from app.graph.state import AgentState
from app.config import get_settings
from app.graph.citations import extract_source_numbers
from app.graph.grounding import citation_correction, require_valid_citations
from app.graph.nodes.evidence import assess_evidence
from app.graph.nodes.fallback import explicit_web_intent
from app.graph.nodes.resolve_fallback import resolve_knowledge_gap
from app.graph.nodes.teach import build_teaching_context
from app.rag.retriever import to_citations
from app.llm import chat, reset_response_draft
from app.prompts import ANSWER_SYSTEM, NO_EVIDENCE_TEXT, build_context


def _generate_with_citation_correction(question: str, context: str, allowed_indices: list[int]) -> str:
    """Generate once, correct invalid citations once, then refuse publication."""
    system = ANSWER_SYSTEM.replace("{context}", context).replace("{no_evidence_text}", NO_EVIDENCE_TEXT)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    allowed_indices = set(allowed_indices)
    response = chat(messages)
    numbers = extract_source_numbers(response)
    if numbers and numbers.issubset(allowed_indices):
        return response
    reset_response_draft()
    messages.extend([
        {"role": "assistant", "content": response},
        {"role": "user", "content": citation_correction(sorted(allowed_indices))},
    ])
    corrected = chat(messages)
    require_valid_citations(corrected, sorted(allowed_indices))
    return corrected


def generate(state: AgentState) -> dict:
    chunks = state.get("retrieved") or []
    question = state.get("question") or state["messages"][-1]["content"]
    if get_settings().enable_knowledge_fallback and explicit_web_intent(question):
        return resolve_knowledge_gap(question)
    if not chunks:
        if get_settings().enable_knowledge_fallback:
            return resolve_knowledge_gap(question)
        return {"answer": NO_EVIDENCE_TEXT, "answer_basis": "none"}
    context = build_context(chunks)
    citations = state.get("citations") or to_citations(chunks)
    if get_settings().enable_knowledge_fallback:
        evidence = assess_evidence(question, chunks)
        if evidence["status"] == "insufficient":
            return resolve_knowledge_gap(question)
        context = build_teaching_context(chunks, evidence["supporting_indices"])
        selected = set(evidence["supporting_indices"])
        citations = [item for item in citations if item["index"] in selected]
    answer = _generate_with_citation_correction(question, context, [item["index"] for item in citations])
    return {"answer": answer, "citations": citations, "answer_basis": "local"}
