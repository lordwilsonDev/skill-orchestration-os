"""Shared DeepSeek transport for skill-orchestration-os.

Extracted from runtime/orchestrator.py so the planner (Orchestrator) and the
routing front-end (DomainRouter) use ONE request implementation instead of two
copies. Same shape as the original: deepseek-chat, temperature 0.2, max_tokens
500, Bearer auth, fence stripping.

Error contract: deepseek_chat RAISES on any failure (missing key, network,
HTTP, parse). The orchestrator wraps it to keep its silent local-fallback
behavior; the router calls it fail-loud (spec §9). No guessing inside here.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TEMP = 0.2
DEEPSEEK_MAX_TOKENS = 500
DEEPSEEK_TIMEOUT_S = 30


def strip_fences(content: str) -> str:
    """Strip ```json ... ``` fences around the model's reply."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 1)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip().rstrip("`").strip()
    return content


def deepseek_chat(
    prompt: str,
    api_key: str | None = None,
    temperature: float = DEEPSEEK_TEMP,
    max_tokens: int = DEEPSEEK_MAX_TOKENS,
    timeout: float = DEEPSEEK_TIMEOUT_S,
) -> str:
    """POST a chat completion and return the model's content, fence-stripped.

    Raises RuntimeError on missing key and OSError/HTTPError on transport
    failures — fail loud. Callers that want a fallback (the orchestrator's
    local planner) wrap this in try/except themselves.
    """
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    payload: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return strip_fences(data["choices"][0]["message"]["content"])
