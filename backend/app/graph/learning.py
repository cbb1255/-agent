"""Compose the learner's functions into a resumable teaching workflow."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.citations import extract_source_numbers
from app.graph.nodes.evidence import assess_evidence
from app.graph.nodes.fallback import explicit_web_intent
from app.config import get_settings
from app.graph.nodes.resolve_fallback import resolve_knowledge_gap
from app.graph.nodes.quiz import generate_quiz
from app.graph.nodes.remediate import generate_remediation
from app.graph.nodes.retrieve import retrieve
from app.graph.nodes.router import classify_goal
from app.graph.nodes.teach import generate_teaching
from app.graph.nodes.variant_quiz import generate_variant_quiz
from app.prompts import NO_EVIDENCE_TEXT


def check_source_numbers(answer: str, allowed_indices: list[int]) -> str:
    """Enforce explicit, valid source numbers on generated grounded prose."""
    numbers = extract_source_numbers(answer)
    if not numbers or not numbers.issubset(set(allowed_indices)):
        raise ValueError("回答缺少有效来源编号，请重试生成。")
    return answer


class LearningState(TypedDict, total=False):
    question: str
    concept_label: str
    concept_id: str
    selected_mode: str | None
    submode: str
    task_id: str
    session_id: str
    goal_class: str
    retrieved: list[dict]
    citations: list[dict]
    evidence: dict
    answer: str
    answer_basis: str
    exercise_id: str
    exercise_number: int
    quiz: dict | None
    grading: dict | None
    user_answer: str
    next_action: str
    last_activity: str
    remediation_rounds: int
    variant_rounds: int


def public_quiz(quiz: dict, exercise_id: str) -> dict:
    return {
        "exercise_id": exercise_id,
        "question": quiz["question"],
        "options": dict(quiz["options"]),
    }


def available_actions(state: dict) -> list[str]:
    if state.get("quiz") is None:
        return ["exercise", "finish"]
    actions = []
    grading = state.get("grading") or {}
    if (grading and not grading["is_correct"]
            and state.get("last_activity") != "remediate"
            and state.get("remediation_rounds", 0) < 2):
        actions.append("remediate")
    if state.get("variant_rounds", 0) < 2:
        actions.append("variant")
    return actions + ["finish"]


def build_learning_graph(store, checkpointer):
    def route(state):
        goal = classify_goal(state["question"], state.get("selected_mode"))
        concept_id = ""
        if goal != "direct":
            concept_id = store.concept(state["question"], state.get("concept_label", ""))
        return {"goal_class": goal, "concept_id": concept_id}

    def direct(state):
        return {"answer": "你好！可以向我提问，或选择教学、测验开始学习。"}

    def check_evidence(state):
        evidence = assess_evidence(state["question"], state["retrieved"])
        selected = set(evidence["supporting_indices"])
        return {
            "evidence": evidence,
            "citations": [item for item in state["citations"] if item["index"] in selected],
            "answer_basis": "local" if evidence["status"] != "insufficient" else "none",
        }

    def after_evidence(state):
        if get_settings().enable_knowledge_fallback and explicit_web_intent(state["question"]):
            return "fallback"
        if state["evidence"]["status"] == "insufficient":
            if get_settings().enable_knowledge_fallback:
                return "fallback"
            return "no_evidence"
        return "make_quiz" if state["goal_class"] == "assess" else "teach"

    def fallback(state):
        result = resolve_knowledge_gap(
            state["question"], require_sources=state["goal_class"] == "assess", submode=state["submode"],
        )
        return {**result, "last_activity": "teach"}

    def after_fallback(state):
        if state["goal_class"] == "teach" and state["answer_basis"] in ("model", "web"):
            return "record_fallback_teaching"
        if state["answer_basis"] == "web":
            return "make_quiz"
        return END

    def record_fallback_teaching(state):
        return {"concept_id": store.record_teaching(state)}

    def after_fallback_teaching(state):
        return "choose" if state["answer_basis"] == "web" else END

    def teach(state):
        answer = generate_teaching(
            state["question"], state["retrieved"], state["evidence"], state["submode"],
        )
        answer = check_source_numbers(answer, state["evidence"]["supporting_indices"])
        concept_id = store.record_teaching(state)
        return {"answer": answer, "last_activity": "teach", "concept_id": concept_id}

    def make_quiz(state):
        number = state.get("exercise_number", 0) + 1
        exercise_id, quiz = store.exercise(
            state, number,
            lambda: generate_quiz(state["question"], state["retrieved"], state["evidence"]),
        )
        return {
            "quiz": quiz, "exercise_id": exercise_id, "exercise_number": number,
            "grading": None, "user_answer": "", "answer": "请选择一个选项并提交答案。",
            "last_activity": "quiz",
        }

    def await_answer(state):
        response = interrupt({
            "kind": "answer", "pending_id": state["exercise_id"] + ":answer",
            "quiz": public_quiz(state["quiz"], state["exercise_id"]),
            "actions": ["answer", "finish"],
        })
        return {"next_action": response["action"], "user_answer": response.get("answer", "")}

    def grade(state):
        result = store.grade(state["exercise_id"], state["quiz"], state["user_answer"])
        label = "回答正确" if result["is_correct"] else "本题答错了"
        return {
            "grading": result, "last_activity": "grade",
            "answer": f"{label}，得分 {result['score']}/1。\n{result['explanation']}",
        }

    def choose(state):
        marker = f"{state['task_id']}:{state['exercise_number']}:{state['last_activity']}"
        response = interrupt({
            "kind": "choice", "pending_id": marker,
            "actions": available_actions(state),
        })
        return {"next_action": response["action"]}

    def remediate(state):
        answer = generate_remediation(state["quiz"], state["grading"], state["retrieved"])
        answer = check_source_numbers(answer, state["quiz"]["source_indices"])
        return {
            "answer": answer, "last_activity": "remediate",
            "remediation_rounds": state["remediation_rounds"] + 1,
        }

    def variant(state):
        number = state["exercise_number"] + 1
        missing = ["巩固原题知识点"]
        if state.get("grading") and not state["grading"]["is_correct"]:
            missing = ["区分原题中用户错选项与正确选项涉及的概念"]
        exercise_id, quiz = store.exercise(
            state, number,
            lambda: generate_variant_quiz(state["quiz"], state["retrieved"], missing),
        )
        return {
            "quiz": quiz, "exercise_id": exercise_id, "exercise_number": number,
            "variant_rounds": state["variant_rounds"] + 1, "grading": None,
            "user_answer": "", "answer": "变式题已准备好，请选择答案。",
            "last_activity": "quiz",
        }

    graph = StateGraph(LearningState)
    for name, node in {
        "route": route, "direct": direct, "retrieve": retrieve,
        "evidence": check_evidence, "teach": teach, "make_quiz": make_quiz,
        "await_answer": await_answer, "grade": grade, "choose": choose,
        "remediate": remediate, "variant": variant,
        "fallback": fallback,
        "record_fallback_teaching": record_fallback_teaching,
        "no_evidence": lambda state: {"answer": NO_EVIDENCE_TEXT, "citations": []},
        "finish": lambda state: {"last_activity": "finished"},
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", lambda state: state["goal_class"], {
        "direct": "direct", "teach": "retrieve", "assess": "retrieve",
    })
    graph.add_edge("direct", END)
    graph.add_edge("retrieve", "evidence")
    graph.add_conditional_edges("evidence", after_evidence)
    graph.add_conditional_edges("fallback", after_fallback)
    graph.add_conditional_edges("record_fallback_teaching", after_fallback_teaching)
    graph.add_edge("no_evidence", END)
    graph.add_edge("teach", "choose")
    graph.add_edge("make_quiz", "await_answer")
    graph.add_conditional_edges("await_answer", lambda state: state["next_action"], {
        "answer": "grade", "finish": "finish",
    })
    graph.add_edge("grade", "choose")
    graph.add_conditional_edges("choose", lambda state: state["next_action"], {
        "exercise": "make_quiz", "remediate": "remediate",
        "variant": "variant", "finish": "finish",
    })
    graph.add_edge("remediate", "choose")
    graph.add_edge("variant", "await_answer")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
