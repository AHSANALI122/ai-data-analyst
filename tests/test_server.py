"""Feature 6 — FastAPI backend.

Drives the HTTP surface with the FastAPI ``TestClient`` and the shared fakes
from ``conftest.py``. No API key is needed: ``agents._ask`` is monkeypatched per
test. ``server`` builds its graph at import, but the nodes call ``agents._ask``
by reference at runtime, so patching the module attribute here still takes
effect (same trick ``conftest.drive`` relies on).
"""

import json

import pytest
from fastapi.testclient import TestClient

import agents
import server
from conftest import VALID_SQL, VALID_ROWS

client = TestClient(server.app)


def _rows_as_lists(rows):
    """VALID_ROWS are tuples; the API serializes rows to JSON arrays (lists)."""
    return [list(r) for r in rows]


def _clear_ask(route):
    """A fake _ask for a clear question: plan -> VALID_SQL -> insight."""

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return json.dumps(
                {"plan": "count by region", "needs_clarification": False,
                 "clarifying_question": ""}
            )
        if node == "sql_generator":
            return VALID_SQL
        return "Counts by region.\nSuggested chart: bar"

    return ask


def test_start_returns_approval_interrupt(route):
    agents._ask = _clear_ask(route)
    r = client.post("/api/start", json={"question": "customers per region"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "interrupt"
    assert data["interrupt"]["type"] == "approval"
    assert data["interrupt"]["sql"] == VALID_SQL
    assert data["thread_id"]


def test_approve_returns_done_with_rows_and_chart(route):
    agents._ask = _clear_ask(route)
    start = client.post("/api/start", json={"question": "customers per region"}).json()
    tid = start["thread_id"]

    r = client.post(
        "/api/resume", json={"thread_id": tid, "resume": {"action": "approve"}}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["rows"] == _rows_as_lists(VALID_ROWS)
    assert data["columns"]
    assert "Suggested chart" in data["insight"]


def test_reject_returns_cancelled(route):
    agents._ask = _clear_ask(route)
    start = client.post("/api/start", json={"question": "customers per region"}).json()
    tid = start["thread_id"]

    r = client.post(
        "/api/resume", json={"thread_id": tid, "resume": {"action": "reject"}}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_edit_runs_edited_sql(route):
    agents._ask = _clear_ask(route)
    start = client.post("/api/start", json={"question": "customers per region"}).json()
    tid = start["thread_id"]

    r = client.post(
        "/api/resume",
        json={"thread_id": tid, "resume": {"action": "edit", "sql": VALID_SQL}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["sql"] == VALID_SQL
    assert data["rows"] == _rows_as_lists(VALID_ROWS)


def test_llm_error_returns_error_card_not_500():
    def boom(system, user):
        raise RuntimeError("model unavailable")

    agents._ask = boom
    r = client.post("/api/start", json={"question": "anything"})
    assert r.status_code == 200  # deliberately not a 500
    data = r.json()
    assert data["status"] == "error"
    assert "model unavailable" in data["message"]


# --------------------------------------------------------------------------- #
# Streaming endpoints (agent progress view)
# --------------------------------------------------------------------------- #

def _read_sse(url, body):
    """POST to an SSE endpoint and collect the parsed data events."""
    events = []
    with client.stream("POST", url, json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_start_stream_emits_nodes_then_interrupt(route):
    agents._ask = _clear_ask(route)
    events = _read_sse("/api/start_stream", {"question": "customers per region"})

    nodes = [e["node"] for e in events if e.get("event") == "node"]
    assert nodes == ["planner", "sql_generator"]  # ordered, live

    # planner event carries the plan; sql_generator event carries the SQL
    planner_evt = next(e for e in events if e.get("node") == "planner")
    assert planner_evt.get("plan")
    sql_evt = next(e for e in events if e.get("node") == "sql_generator")
    assert sql_evt.get("sql") == VALID_SQL

    result = events[-1]
    assert result["event"] == "result"
    assert result["status"] == "interrupt"
    assert result["interrupt"]["type"] == "approval"
    assert result["plan"]


def test_resume_stream_runs_to_done(route):
    agents._ask = _clear_ask(route)
    start = _read_sse("/api/start_stream", {"question": "customers per region"})
    tid = start[-1]["thread_id"]

    events = _read_sse(
        "/api/resume_stream", {"thread_id": tid, "resume": {"action": "approve"}}
    )
    nodes = [e["node"] for e in events if e.get("event") == "node"]
    assert "executor" in nodes and "narrator" in nodes

    result = events[-1]
    assert result["event"] == "result"
    assert result["status"] == "done"
    assert result["rows"] == _rows_as_lists(VALID_ROWS)
    assert "Suggested chart" in result["insight"]


def test_start_stream_error_becomes_result_event():
    def boom(system, user):
        raise RuntimeError("model unavailable")

    agents._ask = boom
    events = _read_sse("/api/start_stream", {"question": "anything"})
    result = events[-1]
    assert result["event"] == "result"
    assert result["status"] == "error"
    assert "model unavailable" in result["message"]
