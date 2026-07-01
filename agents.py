"""Agent nodes and the single LLM entry point.

This module owns *all* model access. The active LLM is chosen at import time
from `LLM_PROVIDER` (google=free Gemini default, or anthropic=Claude) and every
model call goes through `_ask(system, user)`. Tests monkeypatch `_ask`, so no
other code should ever talk to the model directly.

Feature 1 provides only the LLM layer; the node functions arrive in Feature 2.
"""

import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

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
