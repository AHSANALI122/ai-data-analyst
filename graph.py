"""Graph wiring for the AI Data Analyst.

Feature 3 inserts a human-in-the-loop approval gate before execution:

    START -> planner -> sql_generator -> human_approval [interrupt]
                                           reject -> END (cancelled)
                                           approve/edit -> executor -> narrator -> END

The interrupt requires a checkpointer, so the graph now compiles with an
`InMemorySaver`. The debug loop and clarification are layered on in later
features.
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph

import agents
from state import AgentState


def _after_approval(state):
    """Route out of human_approval: reject cancels, anything else executes."""
    return END if state.get("cancelled") else "executor"


def build_graph():
    """Build and compile the pipeline with the HITL approval gate."""
    g = StateGraph(AgentState)

    g.add_node("planner", agents.planner)
    g.add_node("sql_generator", agents.sql_generator)
    g.add_node("human_approval", agents.human_approval)
    g.add_node("executor", agents.executor)
    g.add_node("narrator", agents.narrator)

    g.add_edge(START, "planner")
    g.add_edge("planner", "sql_generator")
    g.add_edge("sql_generator", "human_approval")
    g.add_conditional_edges(
        "human_approval", _after_approval, {END: END, "executor": "executor"}
    )
    g.add_edge("executor", "narrator")
    g.add_edge("narrator", END)

    # A checkpointer is REQUIRED for interrupt/resume. InMemorySaver keeps
    # interrupted threads only while the process lives (see CLAUDE.md gotchas).
    return g.compile(checkpointer=InMemorySaver())
