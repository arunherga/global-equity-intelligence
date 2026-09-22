"""Local Ollama provider. Free, private, and the default when AI is enabled."""

from __future__ import annotations

import json
from typing import Any, Dict

import requests


class OllamaProvider:
    name = "ollama"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.base_url = str(config.get("base_url", "http://localhost:11434")).rstrip("/")
        self.model = str(config.get("model", "llama3.1:8b"))
        self.timeout = int(config.get("timeout_seconds", 90))
        self.temperature = float(config.get("temperature", 0.1))

    def complete(self, system: str, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": self.temperature},
                "format": "json",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return (payload.get("message") or {}).get("content", "")
