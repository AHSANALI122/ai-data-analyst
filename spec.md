# AI Data Analyst — Feature-wise Build Spec

A build plan for **Claude Code**. Each feature below is scoped to fit in **one
session**: it lists its goal, what it depends on, the files to touch, the key
implementation details, and how to verify it before moving on.

Build the features **in order** — each one assumes the previous is done.

> Full reference: see `spec-reference.md` for the consolidated technical spec.

---

## System overview (read at the start of every session)

We are building a multi-agent **AI Data Analyst**: a user asks a question in plain
English; the system plans the analysis, writes SQL, **pauses for human approval**,
runs the query read-only, and explains the result. Failing SQL is repaired by a
**self-correcting debug loop**. One LangGraph powers both a CLI and a web UI.

### Target graph (end state)

```
START -> planner --(ambiguous?)--> clarify [interrupt] --> sql_generator
                 \--------------------------------------> sql_generator
        sql_generator -> human_approval [interrupt: approve | edit | reject]
                           reject -> END(cancelled)
                           approve/edit -> executor
        executor --(ok)--------------> narrator -> END
                 \--(error, tries<MAX)-> debugger -> human_approval
                 \--(error, tries=MAX)-> narrator(reports failure) -> END
```

### Global conventions & invariants (never break these)

1. **Single LLM entry point.** All model calls go through `agents._ask(system, user)`.
   Tests monkeypatch it, so never call the model anywhere else.
2. **Provider-agnostic LLM.** `LLM_PROVIDER=google` (free Gemini, default) or
   `anthropic` (Claude). Never hardcode a provider outside `agents.py`.
3. **Execution is read-only.** All queries run through `database.run_select`, which
   opens the DB with `mode=ro`. No write path, ever. No keyword denylist.
4. **HITL nodes are interrupt-safe.** A node re-runs from its start on resume, so
   `clarify` and `human_approval` call `interrupt()` exactly once with no side
   effects before it.
5. **The debug loop stays bounded** by `MAX_RETRIES` (`graph.py:_after_executor`).
6. **Never clobber a real DB.** Entry points call `ensure_sample_db()` (seed-if-missing),
   never `build_sample_db()` (which deletes).
7. **Resume contract.** clarify resumes with a string; approval resumes with a dict
   `{action, sql?}`. CLI and frontend both depend on this.

### Tech stack
LangGraph · LangChain · Gemini (free) / Claude · FastAPI · SQLite · Chart.js · Python 3.10+

### How to run (once built)
```bash
pip install -r requirements.txt
cp .env.example .env        # add GOOGLE_API_KEY (free: https://aistudio.google.com/apikey)
python server.py            # web UI  -> http://127.0.0.1:8000
python main.py "question"   # CLI
```

### State schema (shared across features)
`AgentState` (TypedDict, all keys optional): `question, plan, needs_clarification,
clarifying_question, clarification, sql, error, retries, columns, result, insight,
cancelled`.

---

# Features

## Feature 0 — Project scaffold + sample database
**Goal:** a runnable project skeleton with a seeded demo DB and a read-only query runner.
**Depends on:** nothing.
**Files:** `requirements.txt`, `.env.example`, `.gitignore`, `database.py`.

**Build:**
- `requirements.txt`: langgraph, langchain-core, langchain-google-genai, python-dotenv,
  tabulate, fastapi, uvicorn. Keep `langchain-anthropic` commented (optional).
- `.env.example`: `GOOGLE_API_KEY`, `LLM_PROVIDER=google`, optional `ANALYST_MODEL`.
- `.gitignore`: `.env`, `store.db`, `__pycache__/`.
- `database.py`:
  - `DB_PATH` constant.
  - `build_sample_db(path)` — DROP+create 4 tables (customers, products, orders,
    order_items) and insert deterministic demo rows. Document that it DELETES the file.
  - `ensure_sample_db(path)` — call `build_sample_db` only if the file is missing.
  - `get_schema_text()` — return a schema description string for the LLM (include the
    revenue = quantity*unit_price note and the 'exclude cancelled' note).
  - `run_select(sql, path)` — open connection with `file:{path}?mode=ro` (uri=True);
    reject anything not starting with select/with (friendly `ValueError`); fetch at
    most `MAX_ROWS` (1000) rows; return `(columns, rows)`.

**Acceptance criteria:**
- `ensure_sample_db()` creates `store.db` when absent and is a no-op when present.
- `run_select` returns rows for a valid SELECT / join.
- `run_select` raises on INSERT/UPDATE/DELETE/DROP and on stacked statements.
- A legit query with a keyword inside a string literal (e.g. `SELECT 'please create x'`)
  is **allowed** (no denylist false positives).

**Verify:**
```bash
python -c "from database import *; ensure_sample_db(); print(run_select('SELECT region,COUNT(*) FROM customers GROUP BY region'))"
```

---

## Feature 1 — State + provider-agnostic LLM layer
**Goal:** shared state type and a single, swappable LLM helper.
**Depends on:** Feature 0.
**Files:** `state.py`, `agents.py` (LLM section only).

**Build:**
- `state.py`: define `AgentState(TypedDict, total=False)` with all keys from the schema above.
- `agents.py` (top only for now):
  - Read `PROVIDER = os.environ.get("LLM_PROVIDER","google").lower()` and `MAX_RETRIES=3`.
  - If `anthropic`: `ChatAnthropic(model=ANALYST_MODEL or "claude-sonnet-4-6", temperature=0, max_tokens=1024)`.
  - Else: `ChatGoogleGenerativeAI(model=ANALYST_MODEL or "gemini-2.5-flash", temperature=0, max_output_tokens=1024)`.
  - `_ask(system, user)` -> str, via `SystemMessage`/`HumanMessage`.
  - `_strip_fences(text)` helper to remove ```sql / ```json fences.

**Acceptance criteria:**
- With `LLM_PROVIDER=google` + a dummy `GOOGLE_API_KEY`, `agents._llm` constructs as `ChatGoogleGenerativeAI`.
- Switching `LLM_PROVIDER=anthropic` selects `ChatAnthropic` (with the optional dep installed).
- `_strip_fences("```sql\nSELECT 1\n```")` returns `SELECT 1`.

**Verify:**
```bash
GOOGLE_API_KEY=dummy python -c "import agents; print(type(agents._llm).__name__, agents.MODEL)"
```

---

## Feature 2 — Core pipeline (no HITL yet)
**Goal:** end-to-end question -> plan -> SQL -> result -> insight, as a straight line.
**Depends on:** Feature 1.
**Files:** `agents.py` (nodes), `graph.py` (minimal), `main.py` (basic CLI).

**Build:**
- `agents.py` nodes:
  - `planner(state)` -> LLM returns JSON `{plan, needs_clarification, clarifying_question}`;
    parse with a fallback; set `retries=0`. (Keep the ambiguity fields; wiring comes in Feature 5.)
  - `sql_generator(state)` -> LLM writes SQLite SQL from schema+plan(+clarification); strip fences.
  - `executor(state)` -> `run_select`; on success set `columns,result,error=None`; on error set
    `error` and `retries+1`.
  - `narrator(state)` -> LLM writes 2–4 sentence insight + a line `Suggested chart: <type>`;
    preview at most 30 rows.
- `graph.py`: linear `planner -> sql_generator -> executor -> narrator -> END`. Compile
  (no checkpointer needed yet).
- `main.py`: build sample DB (via `ensure_sample_db`), invoke once, print table + insight.

**Acceptance criteria:**
- A question returns columns, rows, and an insight string containing `Suggested chart:`.
- No approval/pause yet (that's Feature 3).

**Verify:** run with a mocked `agents._ask` (see Testing note) — full path returns rows + insight.

---

## Feature 3 — Human-in-the-loop approval gate
**Goal:** the agent must get explicit approval before any SQL runs.
**Depends on:** Feature 2.
**Files:** `agents.py` (`human_approval`), `graph.py` (edges + checkpointer), `main.py` (resume loop).

**Build:**
- `agents.py`: `human_approval(state)`:
  - `decision = interrupt({"type":"approval","sql":state["sql"],"actions":["approve","edit","reject"]})`
  - `reject` -> `{"cancelled": True}`; `edit` -> `{"sql": decision["sql"], "cancelled": False}`;
    else -> `{"cancelled": False}`.
- `graph.py`:
  - insert `human_approval` between `sql_generator` and `executor`.
  - conditional after approval: cancelled -> END, else -> executor.
  - compile with `InMemorySaver()` (a checkpointer is REQUIRED for interrupt/resume).
- `main.py`: loop — `graph.invoke(...)`; if `"__interrupt__"` in result, read
  `result["__interrupt__"][0].value`, prompt the user, resume with `Command(resume=...)`.

**Acceptance criteria:**
- Start pauses with an `approval` interrupt exposing the proposed SQL.
- Resuming `{action:"approve"}` runs it and returns results.
- `{action:"reject"}` ends with `cancelled=True` and runs nothing.
- `{action:"edit", sql:...}` runs the edited SQL.

**Verify:** mock `_ask`; assert `"__interrupt__"` present on start, then approve -> rows returned.

---

## Feature 4 — Self-correcting debug loop
**Goal:** when SQL fails, a debugger agent fixes it and re-requests approval; loop is bounded.
**Depends on:** Feature 3.
**Files:** `agents.py` (`debugger`), `graph.py` (routing).

**Build:**
- `agents.py`: `debugger(state)` -> LLM rewrites the failing SQL using schema + failing SQL + error; strip fences.
- `graph.py`: `_after_executor(state)`:
  - no error -> `narrator`.
  - error and `retries < MAX_RETRIES` -> `debugger`.
  - error and `retries >= MAX_RETRIES` -> `narrator` (which reports the failure).
  - edge `debugger -> human_approval` (so the fixed query is re-approved).
- `narrator`: if `state["error"]` is set, return a "could not answer after N attempts" message.

**Acceptance criteria:**
- A query that always errors terminates cleanly (no infinite loop).
- Executor runs **at most `MAX_RETRIES`** times; the count matches the narrator message.
- A fixable error path: bad SQL -> debugger fix -> approve -> success.

**Verify:** mock `_ask` to return broken SQL always; drive the loop approving each time; assert executor call count == `MAX_RETRIES` and a graceful final message.

---

## Feature 5 — Ambiguity clarification
**Goal:** vague questions trigger a clarifying question instead of a wrong guess.
**Depends on:** Feature 4.
**Files:** `agents.py` (`clarify`), `graph.py` (routing).

**Build:**
- `agents.py`: `clarify(state)`:
  - `answer = interrupt({"type":"clarify","question": state["clarifying_question"] or "..."})`
  - return `{"clarification": str(answer), "needs_clarification": False}`.
- `graph.py`:
  - `_after_planner(state)`: `"clarify"` if `needs_clarification` else `"sql_generator"`.
  - edge `clarify -> sql_generator` (forward only — never back to planner; see invariant #4/#5).

**Acceptance criteria:**
- A vague question (e.g. "show top customers") pauses with a `clarify` interrupt.
- After the user answers, the flow continues to SQL generation using the clarification.
- A clear question skips `clarify` entirely.

**Verify:** mock planner to set `needs_clarification=true` for the vague input; assert a `clarify` interrupt.

---

## Feature 6 — FastAPI backend
**Goal:** expose the graph over HTTP with start/resume + clean error handling.
**Depends on:** Feature 5.
**Files:** `server.py`.

**Build:**
- Build one shared `graph` and call `ensure_sample_db()` at import.
- `POST /api/start {question}` -> new `thread_id`, invoke, serialize.
- `POST /api/resume {thread_id, resume}` -> `Command(resume=...)`, invoke, serialize.
  `resume` is `Union[dict, str]`.
- `_serialize(state, thread_id)` -> status ∈ `interrupt | done | cancelled`; `done`
  includes `sql, columns, rows (list-of-lists), insight`.
- Wrap both endpoints in try/except -> return `{status:"error", message}` with HTTP 200.
- Serve `static/index.html` at `/` and mount `/static`.

**Acceptance criteria:**
- `/api/start` returns an `interrupt` (approval) for a normal question.
- `/api/resume` with approve returns `done` + rows.
- A raised LLM error returns `{status:"error"}` (not a 500 stack trace).

**Verify:** FastAPI `TestClient`; mock `_ask`; exercise start -> approve -> done, and an error case.

---

## Feature 7 — Web UI + chart rendering
**Goal:** a zero-build browser UI showing the HITL flow with charts.
**Depends on:** Feature 6.
**Files:** `static/index.html` (HTML+CSS+JS in one file; Chart.js via CDN).

**Build:**
- Question input + example chips (include one vague chip to demo clarify).
- `start()` -> POST `/api/start`; `resume(value)` -> POST `/api/resume`.
- Render by status:
  - `interrupt.type==="clarify"` -> question + text input.
  - `interrupt.type==="approval"` -> SQL block + Approve / Edit / Reject; Edit reveals a textarea.
  - `done` -> result table + Chart.js chart; parse type from the narrator's `Suggested chart:` line
    (fallback bar); pick the last numeric column as the value axis.
  - `cancelled` / `error` -> a simple message card.

**Acceptance criteria:**
- Full happy path works in the browser: ask -> approve -> table + chart + insight.
- Reject and Edit both work from the approval card.
- A vague question shows the clarification card.

**Verify:** `python server.py`, open `http://127.0.0.1:8000`, run the example chips.

---

## Feature 8 — Documentation & repo polish
**Goal:** portfolio-ready repo.
**Depends on:** Feature 7.
**Files:** `README.md`, `CLAUDE.md`, keep `spec.md`/`spec-reference.md` in sync.

**Build:**
- `README.md`: what it is, why it's interesting, architecture, quick start (Gemini free key),
  web UI section, project structure, extend ideas, tech stack.
- `CLAUDE.md`: architecture map, the invariants above, LLM access, testing, gotchas.
- Confirm `.gitignore` covers `.env`, `store.db`, `__pycache__`.

**Acceptance criteria:**
- A new reader can clone, add a free key, and run in under 5 minutes from the README.
- `CLAUDE.md` documents every invariant so future sessions don't regress them.

---

## Testing note (applies to every feature)

No API key is needed to test. Monkeypatch the single LLM entry point and drive the graph:

```python
import os; os.environ["GOOGLE_API_KEY"]="dummy"
import agents
agents._ask = lambda system, user: "<fake response for this node>"
# then invoke the graph / hit the endpoints with a mocked, deterministic LLM
```

Re-verify after each feature: happy path, clarify branch, reject + edit branches,
the debug loop terminates at `MAX_RETRIES`, and the read-only guard blocks writes.
A good final step is to move these inline checks into `pytest` files under `tests/`.

---

## Build-order checklist

- [ ] F0 Scaffold + sample database (read-only guard, no-clobber seeding)
- [ ] F1 State + provider-agnostic LLM (free Gemini default)
- [ ] F2 Core pipeline (planner, sql_generator, executor, narrator)
- [ ] F3 Human-in-the-loop approval gate (interrupt + checkpointer + CLI)
- [ ] F4 Self-correcting debug loop (bounded)
- [ ] F5 Ambiguity clarification
- [ ] F6 FastAPI backend (start/resume + error handling)
- [ ] F7 Web UI + chart rendering
- [ ] F8 Documentation & repo polish
