"""Feature 4 — self-correcting debug loop (bounded)."""

import agents
from conftest import BAD_SQL, VALID_SQL, VALID_ROWS


def test_broken_sql_terminates_at_max_retries(drive, route, planner_json, monkeypatch):
    """Always-broken SQL: the loop stops, executor runs exactly MAX_RETRIES times,
    and the narrator's failure message names that attempt count."""
    calls = {"n": 0}
    orig_run_select = agents.run_select

    def counting_run_select(sql, *a, **k):
        calls["n"] += 1
        return orig_run_select(sql, *a, **k)

    monkeypatch.setattr(agents, "run_select", counting_run_select)

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return planner_json(False)
        if node in ("sql_generator", "debugger"):
            return BAD_SQL
        return "n/a"  # narrator error branch takes no LLM output

    state, interrupts = drive(ask, "how many customers per region")

    assert calls["n"] == agents.MAX_RETRIES
    assert state.get("error")
    assert str(agents.MAX_RETRIES) in state["insight"]
    assert interrupts == ["approval"] * agents.MAX_RETRIES


def test_fixable_error_path(drive, route, planner_json):
    """Bad SQL once, then the debugger fixes it; the fix is re-approved and runs."""

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return planner_json(False)
        if node == "sql_generator":
            return BAD_SQL
        if node == "debugger":
            return VALID_SQL
        return "Counts by region.\nSuggested chart: bar"

    state, interrupts = drive(ask, "how many customers per region")

    assert not state.get("error")
    assert state.get("result") == VALID_ROWS
    # original proposal + the re-approved fix
    assert interrupts == ["approval", "approval"]


def test_happy_path_never_enters_debugger(drive, route, planner_json):
    """Valid SQL first try: one approval, debugger untouched, rows returned."""
    entered = {"debugger": False}

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return planner_json(False)
        if node == "sql_generator":
            return VALID_SQL
        if node == "debugger":
            entered["debugger"] = True
            return VALID_SQL
        return "Counts by region.\nSuggested chart: bar"

    state, interrupts = drive(ask, "how many customers per region")

    assert not entered["debugger"]
    assert not state.get("error")
    assert state.get("result") == VALID_ROWS
    assert interrupts == ["approval"]
