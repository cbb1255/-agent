"""Retrieve evidence using the complete user question."""
import logging
from openai import OpenAIError

from app.config import get_settings
from app.graph.nodes.evidence import assess_evidence
from app.graph.nodes.fallback import explicit_web_intent
from app.graph.state import AgentState
from app.llm import ResponseStreamCancelled, current_request_id
from app.rag.retriever import retrieve as search_documents
from app.rag.retriever import to_citations

LOGGER = logging.getLogger(__name__)
def retrieve(state: AgentState) -> dict:
    question = state.get("question") or state["messages"][-1]["content"]
    settings = get_settings()
    pool_size = settings.evidence_rerank_pool_size
    if pool_size is None or (settings.enable_knowledge_fallback and explicit_web_intent(question)):
        chunks = search_documents(question)
    else:
        candidates = search_documents(question, max_results=pool_size)
        try:
            evidence = assess_evidence(question, candidates, max_sources=settings.rerank_top_n)
            chunks = [candidates[index-1] for index in evidence["supporting_indices"]]
        except ResponseStreamCancelled:
            raise
        except (ValueError, RuntimeError, OpenAIError) as error:
            LOGGER.warning("Evidence selection failed request_id=%s error_type=%s", current_request_id(), type(error).__name__)
            chunks = candidates[:settings.rerank_top_n]
    return {"question": question, "retrieved": chunks, "citations": to_citations(chunks)}
