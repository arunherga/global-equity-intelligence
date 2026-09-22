"""Optional AI layer — interface and prompt.

The deterministic pipeline is complete without this. AI is off by default and
is only ever asked to *explain* an event that deterministic scoring has already
flagged as important. It never scores, never ranks, and never recommends.

The one hard rule, enforced in :func:`sanitise`: no BUY, SELL or HOLD.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

SYSTEM_PROMPT = (
    "You are an equity research assistant. You explain how a specific news "
    "event could affect a specific company's business. You never give "
    "investment advice and never recommend buying, selling or holding a "
    "security. You answer only with valid JSON matching the requested schema. "
    "If something is unknown, say so in the text rather than inventing detail."
)

ANALYSIS_SCHEMA: Dict[str, str] = {
    "revenue_effect": "how revenue could be affected, or 'unclear'",
    "margin_effect": "how margins could be affected, or 'unclear'",
    "cost_effect": "how input or operating costs could be affected",
    "competitive_effect": "how the competitive position could change",
    "regulatory_effect": "any regulatory or compliance consequence",
    "short_term": "implications over the next 0-3 months",
    "medium_long_term": "implications over 3 months to several years",
    "second_order_effects": "knock-on effects that are not obvious",
    "key_uncertainty": "the single biggest thing that is not known",
    "monitor_next": "a list of 3-5 concrete things to watch",
}

PROMPT_TEMPLATE = """Company: {company} ({ticker})
Industry: {industry}
Exchange: {exchange}

Known exposures relevant to this event:
{exposures}

Event: {title}
Event date: {event_date}
Event types: {categories}
Detected relationship to this company: {relationship}
Deterministic impact score: {impact_score}/15
Deterministic direction: {direction}

Source summaries:
{summaries}

Explain why this event matters to THIS company specifically.

Answer with a single JSON object using exactly these keys:
{schema}

"monitor_next" must be a JSON array of strings. Every other value must be a
string. Do not include any text outside the JSON object. Do not recommend
buying, selling or holding.
"""

# Advice language that must never survive into the report.
_FORBIDDEN = re.compile(
    r"\b(buy|sell|hold|accumulate|book profits?|target price|price target|"
    r"overweight|underweight|outperform|underperform)\b",
    re.IGNORECASE,
)


@dataclass
class AiResult:
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    provider: str = ""
    model: str = ""
    redactions: List[str] = field(default_factory=list)


class AiProvider(Protocol):  # pragma: no cover - interface
    name: str

    def complete(self, system: str, prompt: str) -> str:
        """Return the model's raw text response."""


def build_prompt(context: Dict[str, Any]) -> str:
    schema = json.dumps(ANALYSIS_SCHEMA, indent=2)
    return PROMPT_TEMPLATE.format(schema=schema, **context)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the JSON object out of a model response that may be chatty."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = None, 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(text[start : index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    start = None
    return None


def sanitise(data: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    """Strip any recommendation language the model produced anyway."""
    cleaned: Dict[str, Any] = {}
    redactions: List[str] = []

    for key, value in data.items():
        if key not in ANALYSIS_SCHEMA:
            continue
        if key == "monitor_next":
            items = value if isinstance(value, list) else [value]
            kept = []
            for item in items:
                text = str(item).strip()
                if _FORBIDDEN.search(text):
                    redactions.append(f"{key}: removed recommendation language")
                    continue
                kept.append(text)
            cleaned[key] = kept[:5]
            continue

        text = str(value).strip()
        if _FORBIDDEN.search(text):
            text = _FORBIDDEN.sub("[redacted]", text)
            redactions.append(f"{key}: recommendation language redacted")
        cleaned[key] = text[:1200]

    for key in ANALYSIS_SCHEMA:
        cleaned.setdefault(key, [] if key == "monitor_next" else "")
    return cleaned, redactions
