"""OpenAI-compatible provider, and the base for other endpoints that speak
the same chat-completions dialect.

Reads its key from the environment. A key is never read from, or written to,
a configuration file.

Everything an endpoint can differ on is a class attribute rather than a
branch, because "OpenAI-compatible" is a spectrum: the URL, the key's
variable name, the default model, and whether the endpoint honours
``response_format: json_object``. Gemini's compatibility layer, for one,
does not document that parameter, and sending an unknown parameter is how
you turn a working call into a 400.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

import requests

from ..base import send_with_retry


class OpenAiProvider:
    name = "openai"

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    BASE_URL_ENV = "OPENAI_BASE_URL"
    API_KEY_ENV: Tuple[str, ...] = ("OPENAI_API_KEY",)
    DEFAULT_MODEL = "gpt-4o-mini"
    # Ask the endpoint to guarantee JSON. Only where it is actually supported.
    JSON_MODE = True

    def __init__(self, config: Dict[str, Any]) -> None:
        self.base_url = str(
            self._configured_base_url(config)
            or os.environ.get(self.BASE_URL_ENV)
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = str(config.get("model") or self.DEFAULT_MODEL)
        self.timeout = int(config.get("timeout_seconds", 90))
        self.temperature = float(config.get("temperature", 0.1))
        self.api_key = self._key_from_environment()
        self.attempts = int(config.get("max_attempts", 3))
        self.retry_base = float(config.get("retry_base_seconds", 2.0))

    # -- overridable pieces ---------------------------------------------
    def _configured_base_url(self, config: Dict[str, Any]) -> str:
        """``ai.base_url`` belongs to the ollama provider by default.

        A subclass that wants it honoured says so; otherwise a config left
        pointing at localhost:11434 would silently redirect a hosted call.
        """
        return str(config.get("base_url") or "") if self.name == "openai" else ""

    def _key_from_environment(self) -> str:
        for name in self.API_KEY_ENV:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return ""

    # -- the call --------------------------------------------------------
    def complete(self, system: str, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError(f"{self.API_KEY_ENV[0]} is not set")
        body: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.JSON_MODE:
            body["response_format"] = {"type": "json_object"}

        response = send_with_retry(
            lambda: requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            ),
            provider=self.name,
            attempts=self.attempts,
            base_delay=self.retry_base,
        )
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "") or ""
