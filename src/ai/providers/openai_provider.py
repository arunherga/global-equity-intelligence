"""OpenAI-compatible provider.

Reads its key from ``OPENAI_API_KEY`` in the environment. A key is never read
from, or written to, a configuration file.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import requests


class OpenAiProvider:
    name = "openai"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.base_url = str(
            config.get("base_url") or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = str(config.get("model", "gpt-4o-mini"))
        self.timeout = int(config.get("timeout_seconds", 90))
        self.temperature = float(config.get("temperature", 0.1))
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    def complete(self, system: str, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "")
