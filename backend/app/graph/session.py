"""Build local session identifiers and read public graph state."""


def build_thread_config(session_id: str) -> dict:
    LOCAL_USER_PREFIX = "local:"
    if not session_id.strip() or len(session_id.strip())>128:
        raise ValueError
    thread_id=f"{LOCAL_USER_PREFIX}{session_id.strip()}"
    return {"configurable":{"thread_id":thread_id}}
def get_session_view(graph, session_id: str) -> dict:
    config = build_thread_config(session_id)
    state = graph.get_state(config).values
    return {
        "exists": bool(state),
        "question": state.get("question", ""),
        "answer": state.get("answer", ""),
        "citations": state.get("citations", []),
    }
