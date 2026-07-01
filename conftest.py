"""Shared pytest fixtures and fakes for the AI Data Analyst test suite.

No API key is required: the single LLM entry point ``agents._ask`` is replaced
per test with a deterministic fake, and the graph is driven through its
human-in-the-loop interrupts exactly as the CLI does.

This file lives at the project root so its directory is importable (pytest adds
it to ``sys.path``) and the env vars below are set *before* ``agents`` is
imported — ``agents`` constructs the LLM client at import time, so the provider
key must already be present.
"""

import json
import os
import uuid

os.environ.setdefault("GOOGLE_API_KEY", "dummy")
os.environ.setdefault("LLM_PROVIDER", "google")

import pytest
from langgraph.types import Command

import agents
import graph as graph_mod
from database import ensure_sample_db

# Deterministic SQL the fakes hand back. VALID_SQL runs against the seeded demo
# DB; BAD_SQL always fails ("no such table: nope").
VALID_SQL = "SELECT region, COUNT(*) AS n FROM customers GROUP BY region"
VALID_ROWS = [("North", 2), ("South", 2), ("West", 1)]
BAD_SQL = "SELECT * FROM nope"


@pytest.fixture(scope="session", autouse=True)
def _seed_db():
    """Seed the demo DB once for the whole session (no-clobber)."""
    ensure_sample_db()


def _route(system):
    """Map a node's system prompt to the node name, so one fake can serve all."""
    s = system.lower()
    if "planner" in s:
        return "planner"
    if "fixing a query that failed" in s:
        return "debugger"
    if "explain the query result" in s:
        return "narrator"
    if "sqlite expert" in s:
        return "sql_generator"
    return "?"


@pytest.fixture
def route():
    return _route


@pytest.fixture
def planner_json():
    """Build the planner's JSON reply with the given ambiguity flag/question."""

    def _make(needs_clarification, clarifying_question=""):
        return json.dumps(
            {
                "plan": "count customers by region",
                "needs_clarification": needs_clarification,
                "clarifying_question": clarifying_question,
            }
        )

    return _make


@pytest.fixture
def drive():
    """Return a driver that runs a question to completion under a fake ``_ask``.

    Auto-approves every approval interrupt and auto-answers every clarify
    interrupt with ``clarify_answer``. Returns ``(final_state, interrupt_types)``
    where ``interrupt_types`` is the ordered list of interrupt payload types seen
    (e.g. ``["clarify", "approval"]``).
    """

    def _drive(scenario_ask, question, clarify_answer="only completed orders"):
        agents._ask = scenario_ask
        g = graph_mod.build_graph()
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        result = g.invoke({"question": question}, config)

        interrupts = []
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            interrupts.append(payload["type"])
            if payload["type"] == "clarify":
                result = g.invoke(Command(resume=clarify_answer), config)
            else:
                result = g.invoke(Command(resume={"action": "approve"}), config)
        return result, interrupts

    return _drive
