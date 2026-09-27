"""Google Gemini, through its OpenAI-compatible endpoint.

Why this exists: Gemini's API key comes from Google AI Studio with a free
tier and no card, which makes it the one hosted option available when a
payment method is not. It speaks the chat-completions dialect, so it is a
handful of overrides on :class:`OpenAiProvider` rather than a new client.

Two deliberate differences:

* ``JSON_MODE`` is off. Google's compatibility page documents ``model``,
  ``messages``, ``stream``, ``tools``, ``tool_choice``, ``reasoning_effort``
  and ``service_tier`` - not ``response_format: json_object``. Sending it
  anyway risks a 400 on every call. The prompt already demands JSON and
  :func:`src.ai.base.extract_json` handles a chatty answer, including one
  wrapped in a code fence, so nothing is lost but the guarantee.
* ``ai.base_url`` is ignored, as it is for OpenAI, so a config still
  pointing at a local Ollama cannot silently capture a hosted call. Override
  with ``GEMINI_BASE_URL`` if you route through a proxy.

Free tiers carry per-day and per-minute request limits. At the default
``max_events_per_run: 12`` a day is roughly 24 calls across both slots,
which is small - but the limits are Google's and they change, so treat a
429 in Run Diagnostics as a rate limit rather than a bug.
"""

from __future__ import annotations

from .openai_provider import OpenAiProvider


class GeminiProvider(OpenAiProvider):
    name = "gemini"

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    BASE_URL_ENV = "GEMINI_BASE_URL"
    # GOOGLE_API_KEY is accepted second because Google's own SDKs use it.
    API_KEY_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    # Confirm against what your key can reach; --check-ai names a bad id.
    DEFAULT_MODEL = "gemini-3.1-flash-lite"
    JSON_MODE = False
