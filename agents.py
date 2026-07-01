"""Agent nodes and the single LLM entry point.

This module owns *all* model access. The active LLM is chosen at import time
from `LLM_PROVIDER` (google=free Gemini default, or anthropic=Claude) and every
model call goes through `_ask(system, user)`. Tests monkeypatch `_ask`, so no
other code should ever talk to the model directly.

Feature 1 provides only the LLM layer; the node functions arrive in Feature 2.
"""

import json
import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from tabulate import tabulate

from database import get_schema_text, run_select

load_dotenv()

PROVIDER = os.environ.get("LLM_PROVIDER", "google").lower()
ANALYST_MODEL = os.environ.get("ANALYST_MODEL")
MAX_RETRIES = 3

if PROVIDER == "anthropic":
    from langchain_anthropic import ChatAnthropic

    MODEL = ANALYST_MODEL or "claude-sonnet-4-6"
    _llm = ChatAnthropic(model=MODEL, temperature=0, max_tokens=1024)
else:
    from langchain_google_genai import ChatGoogleGenerativeAI

    MODEL = ANALYST_MODEL or "gemini-2.5-flash"
    _llm = ChatGoogleGenerativeAI(model=MODEL, temperature=0, max_output_tokens=1024)


def _ask(system, user):
    """Single entry point for all LLM calls. Returns the response text.

    Tests monkeypatch this function, so keep it the only place that invokes the
    model.
    """
    response = _llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return response.content


def _strip_fences(text):
    """Remove leading/trailing markdown code fences (```sql, ```json, ```)."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Graph nodes (Feature 2 — core pipeline)
#
# Each node takes the shared AgentState and returns a *partial* dict of updates
# (LangGraph merges it into state). Nodes never mutate state in place, and every
# model call goes through `_ask`.
# --------------------------------------------------------------------------- #


def planner(state):
    """Turn the question into a short analysis plan (and flag ambiguity).

    Asks the LLM for JSON `{plan, needs_clarification, clarifying_question}`. The
    ambiguity fields are kept for Feature 5; nothing routes on them yet. On any
    parse failure we fall back to treating the raw text as the plan so the
    pipeline never dies on malformed JSON.
    """
    system = (
        "You are a data analyst planner. Given a database schema and a user's "
        "question, produce a short analysis plan. Decide whether the question is "
        "too ambiguous to answer without clarification.\n"
        "Respond with ONLY a JSON object with keys: "
        '"plan" (string, a brief approach), '
        '"needs_clarification" (boolean), '
        '"clarifying_question" (string, empty unless clarification is needed).'
    )
    user = f"{get_schema_text()}\nQuestion: {state['question']}"
    raw = _ask(system, user)

    try:
        data = json.loads(_strip_fences(raw))
        plan = str(data.get("plan", "")).strip()
        needs_clarification = bool(data.get("needs_clarification", False))
        clarifying_question = str(data.get("clarifying_question", "")).strip()
    except (json.JSONDecodeError, TypeError, AttributeError):
        plan = _strip_fences(raw)
        needs_clarification = False
        clarifying_question = ""

    return {
        "plan": plan,
        "needs_clarification": needs_clarification,
        "clarifying_question": clarifying_question,
        "retries": 0,
    }


def sql_generator(state):
    """Write a single SQLite SELECT from the schema, plan, and question."""
    system = (
        "You are a SQLite expert. Write a single read-only SELECT (or WITH) query "
        "that answers the user's question using the given schema. Return ONLY the "
        "SQL, with no prose, comments, or markdown fences."
    )
    parts = [
        get_schema_text(),
        f"Question: {state['question']}",
        f"Plan: {state.get('plan', '')}",
    ]
    if state.get("clarification"):
        parts.append(f"Clarification: {state['clarification']}")
    sql = _strip_fences(_ask(system, "\n".join(parts)))
    return {"sql": sql}


def executor(state):
    """Run the proposed SQL read-only; capture columns/rows or the error.

    The retry counter is incremented on failure so the Feature 4 debug loop can
    branch on it without reworking this node.
    """
    try:
        columns, rows = run_select(state["sql"])
        return {"columns": columns, "result": rows, "error": None}
    except Exception as e:
        return {"error": str(e), "retries": state.get("retries", 0) + 1}


def debugger(state):
    """Repair a failing query using the schema, failing SQL, and error (Feature 4).

    Mirrors `sql_generator`'s output contract (a single read-only SELECT/WITH,
    no prose or fences) but is handed the query that failed and its error so the
    model can correct it. Returns only the rewritten SQL — the retry counter is
    owned by `executor` (which increments it on failure) and is what bounds the
    debug loop, so we must not touch `retries` or `error` here.
    """
    system = (
        "You are a SQLite expert fixing a query that failed. Given the schema, "
        "the original question, the failing SQL, and the error message, rewrite "
        "the query so it runs correctly and still answers the question. It must "
        "be a single read-only SELECT (or WITH) query. Return ONLY the corrected "
        "SQL, with no prose, comments, or markdown fences."
    )
    user = "\n".join(
        [
            get_schema_text(),
            f"Question: {state['question']}",
            f"Failing SQL: {state.get('sql', '')}",
            f"Error: {state.get('error', '')}",
        ]
    )
    sql = _strip_fences(_ask(system, user))
    return {"sql": sql}


def narrator(state):
    """Explain the result in plain English (or report a failure)."""
    if state.get("error"):
        insight = (
            f"Sorry - I couldn't answer that after {MAX_RETRIES} attempts. "
            f"The query kept failing with: {state['error']}"
        )
        return {"insight": insight}

    columns = state.get("columns", [])
    rows = state.get("result", []) or []
    preview = tabulate(rows[:30], headers=columns, tablefmt="github")

    system = (
        "You are a data analyst. Explain the query result to a non-technical "
        "reader in 2-4 short sentences. Do not mention SQL. After the explanation, "
        "add a final line in exactly this format: 'Suggested chart: <type>' where "
        "<type> is one of bar, line, pie, or none."
    )
    user = f"Question: {state['question']}\n\nResult (up to 30 rows):\n{preview}"
    insight = _ask(system, user).strip()
    return {"insight": insight}


def clarify(state):
    """Pause to ask the user a clarifying question for a vague request (Feature 5).

    Interrupt-safe like `human_approval`: `interrupt()` is the very first action,
    called exactly once with no LLM call or DB write before it, so the re-run on
    resume stays side-effect-free (invariant #4).

    Resume contract: the caller resumes with a plain string (the user's answer),
    not a dict (invariant #7). We record it as `clarification` and clear
    `needs_clarification` so generation proceeds forward without re-clarifying.
    """
    answer = interrupt(
        {
            "type": "clarify",
            "question": state.get("clarifying_question")
            or "Could you clarify what you're looking for?",
        }
    )
    return {"clarification": str(answer), "needs_clarification": False}


def human_approval(state):
    """Pause for explicit human approval before any SQL runs (Feature 3).

    This node is interrupt-safe: it calls `interrupt()` exactly once as its very
    first action, with no LLM call or DB write before it. On resume the node
    re-runs from the top, so keeping the interrupt side-effect-free avoids
    double execution.

    Resume contract: the caller resumes with a dict `{action, sql?}` where
    action is one of approve | edit | reject. The CLI and web frontend both
    depend on this shape.
    """
    decision = interrupt(
        {
            "type": "approval",
            "sql": state["sql"],
            "actions": ["approve", "edit", "reject"],
        }
    )

    action = decision.get("action")
    if action == "reject":
        return {"cancelled": True}
    if action == "edit":
        return {"sql": decision["sql"], "cancelled": False}
    return {"cancelled": False}  # approve
