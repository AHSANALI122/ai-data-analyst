"""FastAPI backend for the AI Data Analyst (Feature 6).

Exposes the LangGraph pipeline over HTTP with a start/resume pair that mirrors
the CLI's interrupt/resume loop, plus static serving for the web UI (Feature 7).

Two endpoints drive the whole human-in-the-loop flow:

- ``POST /api/start``  {question}                 -> new thread, first pause/result
- ``POST /api/resume`` {thread_id, resume}         -> continue that thread

``resume`` is intentionally ``Union[dict, str]`` because the resume payload
depends on the interrupt type (invariant #7): a plain string answers a
``clarify`` interrupt; a dict ``{action, sql?}`` answers an ``approval`` one.

Both endpoints are wrapped so any raised error (e.g. an LLM failure) comes back
as ``{"status": "error", "message": ...}`` with HTTP 200 — the frontend renders
one uniform message card instead of having to parse a 500 stack trace.
"""

import json
import uuid
from typing import Any, Dict, Iterator, List, Union

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langgraph.types import Command

from database import ensure_sample_db
from graph import build_graph

# Seed the demo DB (no-clobber) and build one shared graph at import time. The
# InMemorySaver checkpointer keeps interrupted threads only while this process
# lives — see CLAUDE.md gotchas; swap to SqliteSaver for durable pauses.
ensure_sample_db()
graph = build_graph()

app = FastAPI(title="AI Data Analyst")


class StartReq(BaseModel):
    question: str


class ResumeReq(BaseModel):
    thread_id: str
    # dict for approval ({action, sql?}), plain string for clarify (invariant #7).
    resume: Union[Dict[str, Any], str]


def _config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _shape(values: Dict[str, Any], thread_id: str, interrupt_val) -> Dict[str, Any]:
    """Build the JSON response from graph state values + an optional interrupt.

    status is one of: interrupt | done | cancelled. Every response also carries
    `plan` so the UI can show what the agent intends to do. (error is produced by
    the endpoint wrappers, not here.)
    """
    plan = values.get("plan") or ""

    if interrupt_val is not None:
        # The interrupt payload already carries `type` (clarify|approval) and,
        # for approval, `sql` — the frontend branches on it directly.
        return {
            "status": "interrupt",
            "thread_id": thread_id,
            "interrupt": interrupt_val,
            "plan": plan,
        }

    if values.get("cancelled"):
        return {"status": "cancelled", "thread_id": thread_id}

    # done — tuples become JSON arrays so rows serialize cleanly.
    rows: List[List[Any]] = [list(r) for r in (values.get("result") or [])]
    return {
        "status": "done",
        "thread_id": thread_id,
        "sql": values.get("sql", ""),
        "columns": values.get("columns") or [],
        "rows": rows,
        "insight": values.get("insight") or "",
        "plan": plan,
    }


def _serialize(state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    """Shape an ``invoke`` result (which embeds ``__interrupt__``) into a response."""
    interrupt_val = (
        state["__interrupt__"][0].value if "__interrupt__" in state else None
    )
    return _shape(state, thread_id, interrupt_val)


def _sse(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _run_stream(inputs, thread_id: str) -> Iterator[str]:
    """Drive the graph with ``stream_mode='updates'`` and emit SSE events.

    Emits one ``{event:"node", node}`` per completed node so the UI can light up
    its progress stepper live, then a final ``{event:"result", ...}`` carrying the
    same shape the JSON endpoints return. Any error becomes a terminal ``result``
    event with ``status:"error"`` (mirrors the JSON endpoints' HTTP-200 policy).
    """
    config = _config(thread_id)
    interrupt_val = None
    try:
        for chunk in graph.stream(inputs, config, stream_mode="updates"):
            for key, val in chunk.items():
                if key == "__interrupt__":
                    interrupt_val = val[0].value
                else:
                    evt = {"event": "node", "node": key}
                    # surface the plan/SQL as soon as the node produces it so the
                    # UI can show them live rather than only at the final result.
                    if isinstance(val, dict):
                        if key == "planner" and val.get("plan"):
                            evt["plan"] = val["plan"]
                        if key in ("sql_generator", "debugger") and val.get("sql"):
                            evt["sql"] = val["sql"]
                    yield _sse(evt)
        values = graph.get_state(config).values
        yield _sse({"event": "result", **_shape(values, thread_id, interrupt_val)})
    except Exception as e:
        yield _sse({"event": "result", "status": "error", "message": str(e)})


@app.post("/api/start")
def start(req: StartReq) -> Dict[str, Any]:
    """Begin a new analysis. Returns the first interrupt or a terminal result."""
    try:
        thread_id = str(uuid.uuid4())
        state = graph.invoke({"question": req.question}, _config(thread_id))
        return _serialize(state, thread_id)
    except Exception as e:  # surface LLM/graph errors as a clean card, not a 500
        return {"status": "error", "message": str(e)}


@app.post("/api/resume")
def resume(req: ResumeReq) -> Dict[str, Any]:
    """Resume a paused thread with the user's answer/decision."""
    try:
        state = graph.invoke(Command(resume=req.resume), _config(req.thread_id))
        return _serialize(state, req.thread_id)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/start_stream")
def start_stream(req: StartReq) -> StreamingResponse:
    """Streaming variant of /api/start: emits live node events then the result."""
    thread_id = str(uuid.uuid4())
    return StreamingResponse(
        _run_stream({"question": req.question}, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/resume_stream")
def resume_stream(req: ResumeReq) -> StreamingResponse:
    """Streaming variant of /api/resume: emits live node events then the result."""
    return StreamingResponse(
        _run_stream(Command(resume=req.resume), req.thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
