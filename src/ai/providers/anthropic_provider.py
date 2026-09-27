"""Anthropic (Claude) provider, on the Messages API.

Reads its key from ``ANTHROPIC_API_KEY`` in the environment. A key is never
read from, or written to, a configuration file, and never logged - an error
raised here carries the status code and the API's own message, not the header.

Two differences from the OpenAI-shaped providers, both required by the
Messages API rather than chosen: the system prompt is a top-level ``system``
field instead of a message with ``role: system``, and ``max_tokens`` is
mandatory. The analysis schema asks for ten fields, several of which the
report truncates at 1200 characters, so the default is sized for all ten
rather than for a terse answer that would be cut off mid-JSON and thrown
away by :func:`src.ai.base.extract_json`.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import requests

API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.base_url = str(
            config.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com/v1"
        ).rstrip("/")
        self.model = str(config.get("model") or DEFAULT_MODEL)
        self.timeout = int(config.get("timeout_seconds", 90))
        self.temperature = float(config.get("temperature", 0.1))
        self.max_tokens = int(config.get("max_output_tokens", 2000))
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def complete(self, system: str, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        response = requests.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            # A wrong model id is the likeliest misconfiguration and the API
            # says so precisely. Surfacing its text is the difference between
            # "AI produced nothing" and "that model name is not available".
            raise RuntimeError(
                f"{response.status_code} from Anthropic: {_error_text(response)}"
            )
        payload = response.json()
        return _first_text(payload)


def _error_text(response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - an error page need not be JSON
        return (getattr(response, "text", "") or "")[:200]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]


def _first_text(payload: Dict[str, Any]) -> str:
    """Concatenate the text blocks of a Messages response.

    ``content`` is a list of blocks, not a string, and a response can carry
    more than one text block. Anything that is not a text block (a tool use,
    a thinking block) is skipped rather than stringified into the JSON the
    caller is about to parse.
    """
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        return ""
    parts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)
