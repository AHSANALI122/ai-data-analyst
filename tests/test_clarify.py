"""Feature 5 — ambiguity clarification."""

from conftest import VALID_SQL, VALID_ROWS


def test_vague_question_pauses_and_uses_clarification(drive, route, planner_json):
    """A vague question interrupts with clarify, and the typed answer is threaded
    into SQL generation before the approval gate."""

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return planner_json(True, "Top by revenue or by order count?")
        if node == "sql_generator":
            # the clarify answer must reach the generator's prompt
            assert "Clarification: only completed orders" in user
            return VALID_SQL
        return "Top customers.\nSuggested chart: bar"

    state, interrupts = drive(ask, "show top customers")

    assert interrupts[0] == "clarify"
    assert "approval" in interrupts
    assert state.get("result") == VALID_ROWS


def test_clear_question_skips_clarify(drive, route, planner_json):
    """A clear question routes straight to SQL generation — no clarify interrupt."""

    def ask(system, user):
        node = route(system)
        if node == "planner":
            return planner_json(False)
        if node == "sql_generator":
            return VALID_SQL
        return "Counts by region.\nSuggested chart: bar"

    state, interrupts = drive(ask, "how many customers per region")

    assert interrupts == ["approval"]
    assert state.get("result") == VALID_ROWS
