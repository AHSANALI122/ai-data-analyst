"""Command-line entry point for the AI Data Analyst.

Feature 3: seed the demo DB (if missing), ask one question, then pause for
human approval before any SQL runs. The user can approve, edit, or reject the
proposed query; on approval the result table and plain-English insight print.

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
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
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
