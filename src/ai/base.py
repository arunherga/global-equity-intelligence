"""Optional AI layer — interface and prompt.

The deterministic pipeline is complete without this. AI is off by default and
is only ever asked to *explain* an event that deterministic scoring has already
flagged as important. It never scores, never ranks, and never recommends.

The one hard rule, enforced in :func:`sanitise`: no BUY, SELL or HOLD.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
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
#
# Matching the bare verbs was too blunt, and it showed: a real analysis of a
# bank closing dormant accounts came back reading "if they [redacted]
# balances". The word was "hold". "Hold balances", "sell products",
# "buyers", "households hold deposits", "holding company" are all ordinary
# business English, and redacting them corrodes the analysis quietly - the
# reader sees a gap and cannot tell whether advice was removed or a verb
# was.
#
# So two tiers. Tier one is language that is only ever a recommendation,
# whatever surrounds it. Tier two is the action verbs, which are advice only
# when they are about the security - so they must appear within the same
# sentence as, and close to, a word for the thing being traded.
_ADVICE_PHRASES = re.compile(
    r"\b("
    r"target price|price target|price objective|"
    r"overweight|underweight|"
    r"rated (?:an? )?(?:buy|sell|hold|outperform|underperform)|"
    r"(?:strong |a |an )?(?:buy|sell|hold) (?:rating|recommendation|call|signal)|"
    r"book profits?|accumulate on dips|average down|averaging down|"
    r"(?:recommend|advise|suggest)(?:s|ed|ing)? (?:buying|selling|holding|"
    r"to buy|to sell|to hold|accumulating|exiting)|"
    r"(?:investors?|shareholders?|traders?|readers?|you) should "
    r"(?:buy|sell|hold|exit|accumulate|avoid)|"
    # First-person advisory framing needs no security noun to be advice:
    # "we would buy at 500" is a recommendation whatever follows it.
    r"(?:we|i|one) would (?:buy|sell|hold|accumulate|exit|avoid|add)|"
    r"(?:we|i) (?:are|am) (?:buying|selling|holding|accumulating|exiting)|"
    r"(?:would|will) be a (?:buyer|seller)"
    r")\b",
    re.IGNORECASE,
)

# The thing being traded. Deliberately excludes "shareholder" and
# "stockist": \b keeps "share" from matching inside them.
_SECURITY = (
    r"(?:stock|stocks|share|shares|scrip|scrips|equity|equities|"
    r"security|securities|counter|position|positions)"
)
_ACTION = (
    r"(?:buy|buys|buying|bought|sell|sells|selling|sold|hold|holds|holding|"
    r"accumulate|accumulates|accumulating|exit|exits|exiting|offload|"
    r"offloads|offloading|outperform|outperforms|underperform|underperforms)"
)
# Within one sentence and 30 characters, in either order. Short on purpose:
# the wider the window, the more ordinary prose gets caught.
_ADVICE_NEAR_SECURITY = re.compile(
    rf"\b{_ACTION}\b[^.\n]{{0,30}}?\b{_SECURITY}\b"
    rf"|\b{_SECURITY}\b[^.\n]{{0,30}}?\b{_ACTION}\b",
    re.IGNORECASE,
)


def _advice_spans(text: str):
    """Every stretch of ``text`` that reads as a recommendation."""
    spans = [m.span() for m in _ADVICE_PHRASES.finditer(text)]
    spans += [m.span() for m in _ADVICE_NEAR_SECURITY.finditer(text)]
    return sorted(spans)


def redact_advice(text: str) -> tuple:
    """``text`` with any recommendation replaced, and whether it changed.

    Errs towards redacting: "the bank may sell shares to raise capital" is a
    legitimate business statement that this will still catch, because the
    alternative - reasoning about intent - is not something a regex can do,
    and a missing clause is a smaller failure than published advice.
    """
    spans = _advice_spans(text)
    if not spans:
        return text, False
    out, cursor = [], 0
    for start, end in spans:
        if start < cursor:          # overlapping matches
            continue
        out.append(text[cursor:start])
        out.append("[redacted]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out), True


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


LOG = logging.getLogger("gei.ai")

# Worth trying again: capacity, rate limits and the transient 5xx family.
# 503 in particular is what a free-tier Gemini key sees under load - eleven
# of twelve analyses were lost to it on 2026-09-28 with no retry in place.
# A 404 on a model name is not here: no amount of waiting fixes a typo.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

MAX_BACKOFF_SECONDS = 30.0


def _retry_after(response) -> float:
    """The server's own instruction, when it gives one."""
    try:
        raw = (response.headers or {}).get("Retry-After")
    except Exception:  # noqa: BLE001 - a stub response need not have headers
        return 0.0
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0          # an HTTP-date form; the backoff below covers it


def backoff_delay(attempt: int, base: float) -> float:
    """Exponential, with jitter so parallel runs do not retry in lockstep."""
    ceiling = min(base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    return round(random.uniform(ceiling / 2, ceiling), 2)


def send_with_retry(
    send,
    *,
    provider: str,
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep=None,
):
    """Call ``send`` until it returns a usable response or the attempts run out.

    Retries the transient statuses above and anything that raised on the way
    out - a dropped connection is as temporary as a 503. Everything else is
    raised immediately, because retrying a bad model name only spends the
    budget more slowly.

    Raises ``RuntimeError`` carrying the API's own message, so the report's
    Run Diagnostics says what actually happened rather than a status code.
    """
    sleep = sleep or time.sleep
    attempts = max(1, int(attempts))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = send()
        except Exception as exc:  # noqa: BLE001 - transport errors retry too
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == attempts:
                raise RuntimeError(f"{provider} unreachable: {last_error}") from exc
            delay = backoff_delay(attempt, base_delay)
            LOG.info("%s failed (%s); retrying in %.1fs", provider, last_error, delay)
            sleep(delay)
            continue

        status = int(getattr(response, "status_code", 0) or 0)
        if status < 400:
            return response

        message = api_error_text(response)
        if status not in RETRY_STATUSES or attempt == attempts:
            raise RuntimeError(f"{status} from {provider}: {message}")

        delay = _retry_after(response) or backoff_delay(attempt, base_delay)
        LOG.info(
            "%s returned %d (%s); attempt %d of %d, retrying in %.1fs",
            provider, status, message[:80], attempt, attempts, delay,
        )
        sleep(delay)

    raise RuntimeError(f"{provider} gave up after {attempts} attempts: {last_error}")


def api_error_text(response) -> str:
    """The API's own explanation of a failed call, trimmed.

    A generic "400 Client Error" hides the one thing worth knowing: which
    model name was rejected, or that the key is not valid. Never includes a
    header, so a key cannot reach a log through an exception message.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - an error page need not be JSON
        return (getattr(response, "text", "") or "")[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]


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
                if _advice_spans(text):
                    redactions.append(f"{key}: removed recommendation language")
                    continue
                kept.append(text)
            cleaned[key] = kept[:5]
            continue

        text = str(value).strip()
        text, changed = redact_advice(text)
        if changed:
            redactions.append(f"{key}: recommendation language redacted")
        cleaned[key] = text[:1200]

    for key in ANALYSIS_SCHEMA:
        cleaned.setdefault(key, [] if key == "monitor_next" else "")
    return cleaned, redactions
