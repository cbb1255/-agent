"""Shared state for a single knowledge question."""
import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    messages: Annotated[list[dict], operator.add]
    retrieved: list[dict]
    citations: list[dict]
    answer: str
    answer_basis: str
