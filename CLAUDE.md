# CLAUDE.md

Guidance for working in this repository (read before making changes).

## What this project is

A multi-agent **AI Data Analyst** built on LangGraph: natural-language question →
plan → SQL → human approval → execution → plain-English insight, with a
self-correcting debug loop. One graph powers both a CLI and a web UI.

See `spec.md` for the full specification and `README.md` for usage.

## How to run

```bash
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY

python server.py              # web UI at http://127.0.0.1:8000  (preferred)
python main.py "your question"   # CLI
```

The demo SQLite DB (`store.db`) is auto-seeded on first run via `ensure_sample_db()`.
Delete `store.db` to reseed after changing the schema in `database.py`.

## Architecture map

| File | Role | Touch it when… |
|------|------|----------------|
| `state.py` | `AgentState` TypedDict | adding a field shared between nodes |
| `database.py` | sample DB, schema text, read-only `run_select` | changing data, schema, or query safety |
| `agents.py` | the 7 node functions + LLM helper (`_ask`) | changing agent behaviour or prompts |
| `graph.py` | graph wiring, edges, routing, checkpointer | changing control flow |
| `main.py` | CLI interrupt/resume loop | CLI UX |
| `server.py` | FastAPI endpoints + static serving | API or backend behaviour |
| `static/index.html` | zero-build web UI + Chart.js | frontend |

## Conventions & invariants (do not break)

- **HITL nodes stay interrupt-safe.** A node re-runs from its start on resume, so
  `clarify` and `human_approval` must call `interrupt()` exactly once with no
  side effects before it. Don't add LLM calls or DB writes ahead of the interrupt.
- **No new loops back into `planner`.** `clarify` routes forward to `sql_generator`.
  Routing back to `planner` risks an interrupt-replay loop.
- **The debug loop must stay bounded.** Keep the `executor → debugger → human_approval`
  cycle gated by `retries < MAX_RETRIES` (in `graph.py:_after_executor`). If you
  change `MAX_RETRIES`, keep the narrator's "after N attempts" message consistent.
- **Execution is read-only.** All queries go through `database.run_select`, which
  opens the DB with `mode=ro`. Never add a separate write path. Safety is enforced
  by the engine, not by keyword matching — don't reintroduce a denylist.
- **Never clobber a real DB.** Entry points call `ensure_sample_db()` (seed-if-missing),
  not `build_sample_db()` (which deletes). Keep that distinction.
- **Resume contract.** clarify resumes with a string; approval resumes with a dict
  `{action, sql?}`. The frontend and `main.py` both depend on this — change both
  if you change the contract (and update `spec.md` §4).

## LLM access

- **Provider-agnostic.** `agents.py` picks the backend from `LLM_PROVIDER`:
  `google` (default, free Gemini — key at https://aistudio.google.com/apikey) or
  `anthropic` (Claude — uncomment `langchain-anthropic` in requirements first).
- Model is set via `ANALYST_MODEL` (defaults: `gemini-2.5-flash` for google,
  `claude-sonnet-4-6` for anthropic).
- All model calls go through `agents._ask(system, user)`. Tests monkeypatch this
  function, so keep that single entry point — don't call the LLM directly elsewhere.

## Testing

Run the suite with `pytest` (dev dep in `requirements.txt`) — no API key needed.
Tests live under `tests/`; shared fixtures are in the root `conftest.py`, which
sets a dummy key and swaps `agents._ask` for a per-test fake, then drives the
compiled graph through its interrupts like the CLI does.

- `conftest.py` — `route` (system prompt → node name), `planner_json`, and
  `drive(fake_ask, question)` → `(final_state, interrupt_types)`.
- `tests/test_debug_loop.py` — F4: bounded at `MAX_RETRIES`, fixable path, happy
  path never enters `debugger`.
- `tests/test_clarify.py` — F5: vague question interrupts + threads the answer
  into SQL; clear question skips `clarify`.
- `tests/test_database.py` — `run_select` blocks writes/stacked statements, allows
  keyword-like literals; `ensure_sample_db()` doesn't clobber an existing DB.

When changing agent behaviour, extend the matching fake in the relevant test (the
`route()` helper keys off substrings of each node's system prompt, so keep those
prompts distinctive). Still worth adding: reject/edit approval branches.

## Gotchas

- `InMemorySaver` keeps interrupted threads only while the server process lives.
  For durable pauses, switch to `SqliteSaver` in `graph.py:build_graph`.
- The frontend parses the chart type from the narrator's `Suggested chart: …` line.
  If you change the narrator's output format, update `parseChartType` in the UI.
- The active LLM is constructed at import in `agents.py`, so the relevant key
  (`GOOGLE_API_KEY` for Gemini, `ANTHROPIC_API_KEY` for Claude) must be set; tests
  set a dummy value and monkeypatch `_ask`.

## Out of scope for now (by decision)

Authentication, multi-tenancy, and write operations are intentionally deferred.
Per-session `thread_id` isolation is the foundation to add auth later — see
`spec.md` §9.
