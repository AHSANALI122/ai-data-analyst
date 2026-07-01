"""Graph wiring for the AI Data Analyst.

Features 4 and 5 complete the target control flow:

    START -> planner --(needs_clarification?)--> clarify [interrupt] --> sql_generator
                     \\-------------------------------------------------> sql_generator
             sql_generator -> human_approval [interrupt: approve|edit|reject]
                                reject -> END (cancelled)
                                approve/edit -> executor
             executor --(ok)-----------------> narrator -> END
                      \\--(error, retries<MAX)-> debugger -> human_approval
                      \\--(error, retries>=MAX)-> narrator (reports failure) -> END

The interrupts require a checkpointer, so the graph compiles with an
`InMemorySaver`.
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph

import agents
from state import AgentState


def _after_planner(state):
    """Route out of planner: ask for clarification when the question is vague.

    Forward-only — clarify continues to sql_generator, never back to planner
    (invariants #4/#5), which would risk an interrupt-replay loop.
    """
    return "clarify" if state.get("needs_clarification") else "sql_generator"


def _after_approval(state):
    """Route out of human_approval: reject cancels, anything else executes."""
    return END if state.get("cancelled") else "executor"


def _after_executor(state):
    """Route out of executor: success narrates; failure debugs while retries remain.

    The debug loop is bounded solely here by `retries < MAX_RETRIES`. The
    executor increments `retries` on each failure, so once the cap is reached we
    fall through to the narrator, which reports the failure gracefully.
    """
    if not state.get("error"):
        return "narrator"
    if state.get("retries", 0) < agents.MAX_RETRIES:
        return "debugger"
    return "narrator"


def build_graph():
    """Build and compile the pipeline with the HITL approval gate."""
    g = StateGraph(AgentState)

    g.add_node("planner", agents.planner)
    g.add_node("clarify", agents.clarify)
    g.add_node("sql_generator", agents.sql_generator)
    g.add_node("human_approval", agents.human_approval)
    g.add_node("executor", agents.executor)
    g.add_node("debugger", agents.debugger)
    g.add_node("narrator", agents.narrator)

    g.add_edge(START, "planner")
    g.add_conditional_edges(
        "planner",
        _after_planner,
        {"clarify": "clarify", "sql_generator": "sql_generator"},
    )
    g.add_edge("clarify", "sql_generator")
    g.add_edge("sql_generator", "human_approval")
    g.add_conditional_edges(
        "human_approval", _after_approval, {END: END, "executor": "executor"}
    )
    g.add_conditional_edges(
        "executor",
        _after_executor,
        {"narrator": "narrator", "debugger": "debugger"},
    )
    g.add_edge("debugger", "human_approval")
    g.add_edge("narrator", END)

    # A checkpointer is REQUIRED for interrupt/resume. InMemorySaver keeps
    # interrupted threads only while the process lives (see CLAUDE.md gotchas).
    return g.compile(checkpointer=InMemorySaver())
