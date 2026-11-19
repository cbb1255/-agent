"""Build the retrieval and grounded generation workflow."""
from langgraph.graph import END, START, StateGraph
from app.graph.nodes.generate import generate
from app.graph.nodes.retrieve import retrieve
from app.graph.state import AgentState


def build_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile(checkpointer=checkpointer)
