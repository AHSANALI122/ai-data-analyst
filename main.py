"""Command-line entry point for the AI Data Analyst.

Seed the demo DB (if missing), ask one question, then drive the human-in-the-loop
pauses to completion. Two interrupt types can occur:

- ``clarify`` (Feature 5) — the question was vague; resume with a plain string.
- ``approval`` (Feature 3) — approve/edit/reject the proposed SQL; resume with a
  dict. Rejection cancels; a failing query is repaired by the Feature 4 debug
  loop and re-approved until it succeeds or the retry cap is reached.

Usage:
    python main.py "your question"
"""

import sys
import uuid

from langgraph.types import Command
from tabulate import tabulate

from database import ensure_sample_db
from graph import build_graph

DEFAULT_QUESTION = "How many customers are in each region?"


def _prompt_clarify(payload):
    """Show the clarifying question and collect the user's free-text answer.

    Returns a plain string per the clarify resume contract (invariant #7).
    """
    print(payload.get("question", "Could you clarify what you're looking for?"))
    return input("> ").strip()


def _prompt_approval(payload):
    """Show the proposed SQL and collect an approve/edit/reject decision.

    Returns the resume dict `{action, sql?}` per the approval resume contract.
    """
    print("Proposed SQL:")
    print(f"  {payload['sql']}\n")

    while True:
        choice = input("Approve / Edit / Reject? [a/e/r]: ").strip().lower()
        if choice in ("a", "approve", ""):
            return {"action": "approve"}
        if choice in ("r", "reject"):
            return {"action": "reject"}
        if choice in ("e", "edit"):
            edited = input("Enter the edited SQL:\n  ").strip()
            if edited:
                return {"action": "edit", "sql": edited}
            print("Empty SQL; keeping the original. Choose again.")
            continue
        print("Please answer a (approve), e (edit), or r (reject).")


def main():
    # Seed-if-missing only — never clobber an existing DB.
    ensure_sample_db()

    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION
    print(f"Question: {question}\n")

    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    result = graph.invoke({"question": question}, config)

    # Drive the human-in-the-loop pauses until the graph runs to completion.
    # The resume payload depends on the interrupt type: a plain string for
    # clarify, a dict for approval.
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        if payload.get("type") == "clarify":
            resume = _prompt_clarify(payload)
        else:
            resume = _prompt_approval(payload)
        result = graph.invoke(Command(resume=resume), config)

    print()
    if result.get("cancelled"):
        print("Cancelled - nothing was run.")
        return

    columns = result.get("columns")
    rows = result.get("result")
    if columns and rows is not None:
        print(tabulate(rows, headers=columns, tablefmt="github"))
        print()

    insight = result.get("insight")
    if insight:
        print(insight)


if __name__ == "__main__":
    main()
