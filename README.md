# AI Data Analyst

Ask a question in plain English → the agent **plans** the analysis, **writes SQL**,
**pauses for your approval**, runs it **read-only**, and explains the result in
plain English. Failing SQL is repaired by a **self-correcting debug loop**. One
[LangGraph](https://langchain-ai.github.io/langgraph/) powers both a CLI and a
web UI.

> **Why it's interesting:** it's a small but complete *agentic* system with the
> parts that matter in production — human-in-the-loop approval before anything
> runs, a bounded self-correction loop, read-only safety enforced by the engine
> (not a keyword denylist), and a provider-agnostic LLM layer. It runs on a
> **free Gemini key**.

---

## Architecture

```
START -> planner --(ambiguous?)--> clarify [interrupt] --> sql_generator
                 \--------------------------------------> sql_generator
        sql_generator -> human_approval [interrupt: approve | edit | reject]
                           reject -> END (cancelled)
                           approve/edit -> executor
        executor --(ok)--------------> narrator -> END
                 \--(error, tries<MAX)-> debugger -> human_approval
                 \--(error, tries=MAX)-> narrator (reports failure) -> END
```

Seven agent nodes share one `AgentState`. Two nodes **interrupt** for a human:
`clarify` (resume with a string) and `human_approval` (resume with
`{action, sql?}`). The same compiled graph is driven by the CLI's resume loop and
the web backend's start/resume endpoints.

| File | Role |
|------|------|
| `state.py` | `AgentState` TypedDict shared between nodes |
| `database.py` | sample DB, schema text, read-only `run_select` (`mode=ro`) |
| `agents.py` | the 7 node functions + the single LLM entry point `_ask` |
| `graph.py` | graph wiring, routing, checkpointer |
| `main.py` | CLI interrupt/resume loop |
| `server.py` | FastAPI `/api/start` + `/api/resume` + static serving |
| `static/index.html` | zero-build web UI + Chart.js |

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # then add your key
```

Get a **free Gemini API key** at <https://aistudio.google.com/apikey> and put it
in `.env`:

```
GOOGLE_API_KEY=your_key_here
LLM_PROVIDER=google
```

Run the web UI (preferred) or the CLI:

```bash
python server.py                    # web UI -> http://127.0.0.1:8000
python main.py "How many customers are in each region?"   # CLI
```

The demo SQLite DB (`store.db`) is **auto-seeded on first run** — no setup. Delete
it to reseed after changing the schema in `database.py`.

## Web UI

Open <http://127.0.0.1:8000> and click an example chip (one is deliberately vague
to demo the clarify flow). You'll see:

- **Clarify card** — for vague questions, the agent asks a follow-up before guessing.
- **Approval card** — the proposed SQL with **Approve / Edit / Reject**. Nothing
  runs until you approve; Edit lets you tweak the SQL first.
- **Result** — a table, a plain-English insight, and a **Chart.js** chart whose
  type comes from the narrator's `Suggested chart:` line.

## Using Claude instead of Gemini

The LLM layer is provider-agnostic (chosen at import in `agents.py`):

```
LLM_PROVIDER=anthropic
ANALYST_MODEL=claude-sonnet-4-6    # optional
```

Uncomment `langchain-anthropic` in `requirements.txt` and set `ANTHROPIC_API_KEY`.

## Key design decisions

- **Human-in-the-loop first.** No SQL runs without explicit approval; the approval
  and clarify nodes are interrupt-safe (they call `interrupt()` once with no side
  effects before it, so re-running on resume is safe).
- **Read-only by construction.** Every query goes through `database.run_select`,
  which opens the DB with `mode=ro`. Safety is enforced by the engine — no write
  path and no keyword denylist (so `SELECT 'please create x'` is fine).
- **Bounded self-correction.** The `executor → debugger → human_approval` loop is
  gated by `MAX_RETRIES`, so a query that always fails ends gracefully.
- **Single LLM entry point.** All model calls go through `agents._ask`, which makes
  the whole system testable without an API key (tests monkeypatch that one function).

## Testing

```bash
pytest        # no API key needed — the LLM is mocked at agents._ask
```

Tests drive the compiled graph and the FastAPI endpoints through their interrupts,
covering the happy path, the clarify branch, reject/edit approval branches, the
bounded debug loop, and the read-only guard.

## Extend ideas

- **Auth / multi-tenancy** — the per-session `thread_id` is already the isolation
  foundation to build on.
- **Durable pauses** — swap `InMemorySaver` for `SqliteSaver` in `graph.py` so
  interrupted threads survive a restart.
- **More datasets** — point `database.py` at a different schema/seed.

## Tech stack

LangGraph · LangChain · Gemini (free) / Claude · FastAPI · SQLite · Chart.js · Python 3.10+
