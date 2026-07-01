"""Shared graph state.

`AgentState` is the single TypedDict passed between every node in the graph.
All keys are optional (`total=False`) because nodes fill them in progressively:
the planner sets `plan`, the sql_generator sets `sql`, the executor sets
`columns`/`result`/`error`, and so on.
"""

from typing import Any, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    question: str

    # Planner output
    plan: str
    needs_clarification: bool
    clarifying_question: str

    # Clarify (HITL) output
    clarification: str

    # SQL generation / debugging
    sql: str
    error: Optional[str]
    retries: int

    # Executor output
    columns: List[str]
    result: List[Any]

    # Narrator output
    insight: str

    # Approval (HITL) output
    cancelled: bool
