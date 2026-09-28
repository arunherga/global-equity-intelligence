"""Consumer signal: what people say, tracked as a trend rather than events.

Reviews are not developments. One complaint is a person having a bad week;
forty in seven days against a baseline of five is a product problem, and it
shows up weeks before it reaches a quarterly result. That shape - volume and
direction over time - is not what the Event model is for, so this is kept
deliberately separate: it creates no events, touches no impact score, and can
be deleted without the rest of the pipeline noticing.

The polarity read here is a keyword count, and the report says so. It is
meant to sort a week's mentions into "mostly pleased" and "mostly annoyed"
well enough to notice a change of direction. It is not sentiment analysis,
and a single item's label should not be trusted; the aggregate over dozens
is what carries information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .matching import fold
from .models import Article

# Deliberately small and blunt. A longer list would not make a keyword count
# into an understanding of language; it would only make it look like one.
POSITIVE = (
    "love", "loved", "amazing", "excellent", "great", "good", "works",
    "working", "recommend", "recommended", "happy", "impressed", "worth",
    "effective", "improved", "improvement", "best", "favourite", "favorite",
    "repurchase", "value for money", "genuine", "fast delivery",
)
NEGATIVE = (
    "hate", "hated", "terrible", "awful", "bad", "worst", "useless",
    "disappointed", "disappointing", "waste", "waste of money", "fake",
    "counterfeit", "expired", "damaged", "broke", "broken", "refund",
    "return", "returned", "scam", "avoid", "irritation", "rash", "allergic",
    "burning", "hair fall", "hairfall", "no difference", "stopped working",
    "leaked", "leaking", "delayed", "never arrived", "poor quality",
)


@dataclass
class ConsumerSignal:
    """One company's consumer chatter for one run."""

    ticker: str
    mentions: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    engagement: int = 0
    terms: Dict[str, int] = field(default_factory=dict)
    examples: List[Dict[str, str]] = field(default_factory=list)

    @property
    def net(self) -> int:
        return self.positive - self.negative

    @property
    def leaning(self) -> str:
        if self.mentions < 3:
            # Below this there is no aggregate, only anecdotes.
            return "too few to read"
        if self.net >= max(2, self.mentions // 5):
            return "mostly positive"
        if self.net <= -max(2, self.mentions // 5):
            return "mostly negative"
        return "mixed"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["net"] = self.net
        data["leaning"] = self.leaning
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsumerSignal":
        return cls(
            ticker=str(data.get("ticker", "")),
            mentions=int(data.get("mentions", 0)),
            positive=int(data.get("positive", 0)),
            negative=int(data.get("negative", 0)),
            neutral=int(data.get("neutral", 0)),
            engagement=int(data.get("engagement", 0)),
            terms=dict(data.get("terms", {}) or {}),
            examples=list(data.get("examples", []) or []),
        )


def polarity(text: str) -> str:
    """"positive", "negative" or "neutral", by keyword count."""
    folded = fold(text or "")
    if not folded:
        return "neutral"
    good = sum(1 for word in POSITIVE if word in folded)
    bad = sum(1 for word in NEGATIVE if word in folded)
    if good > bad:
        return "positive"
    if bad > good:
        return "negative"
    return "neutral"


def analyse(articles: Sequence[Article]) -> List[ConsumerSignal]:
    """Aggregate consumer posts per company."""
    signals: Dict[str, ConsumerSignal] = {}
    for article in articles:
        tickers = list(article.tickers_hint) or [str(article.raw.get("query_ticker") or "")]
        for ticker in {t for t in tickers if t}:
            signal = signals.setdefault(ticker, ConsumerSignal(ticker=ticker))
            signal.mentions += 1
            signal.engagement += int(article.raw.get("score") or 0) + int(
                article.raw.get("num_comments") or 0
            )
            term = str(article.raw.get("query_term") or article.query or "")
            if term:
                signal.terms[term] = signal.terms.get(term, 0) + 1
            label = polarity(article.text)
            setattr(signal, label, getattr(signal, label) + 1)
            if len(signal.examples) < 3:
                signal.examples.append({
                    "title": article.title[:160],
                    "url": article.url,
                    "source": article.source_name or article.source_domain,
                    "polarity": label,
                })
    return sorted(signals.values(), key=lambda s: (-s.mentions, s.ticker))


# -- persistence, so a trend exists at all --------------------------------


class SentimentStore:
    """One file per day. A signal with no yesterday is not a trend."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, day: date) -> Path:
        return self.root / f"{day.isoformat()}.json"

    def save(self, day: date, signals: Sequence[ConsumerSignal]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(day)
        payload = {"date": day.isoformat(), "signals": [s.to_dict() for s in signals]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load(self, day: date) -> List[ConsumerSignal]:
        path = self.path_for(day)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [ConsumerSignal.from_dict(s) for s in payload.get("signals", []) or []]

    def most_recent_before(self, day: date) -> List[ConsumerSignal]:
        """The latest earlier day on file, however long ago."""
        if not self.root.exists():
            return []
        earlier = sorted(
            p for p in self.root.glob("*.json") if p.stem < day.isoformat()
        )
        if not earlier:
            return []
        try:
            payload = json.loads(earlier[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [ConsumerSignal.from_dict(s) for s in payload.get("signals", []) or []]


@dataclass
class SignalChange:
    """Today against the last day on file."""

    signal: ConsumerSignal
    previous_mentions: int = 0
    previous_net: int = 0

    @property
    def mention_change(self) -> int:
        return self.signal.mentions - self.previous_mentions

    @property
    def net_change(self) -> int:
        return self.signal.net - self.previous_net

    @property
    def is_notable(self) -> bool:
        """Worth a reader's attention, rather than ordinary week-to-week drift.

        Requires a real baseline: going from one mention to three is noise
        dressed as a 200% rise, and that is exactly the trap a volume metric
        sets.
        """
        if self.signal.mentions < 3:
            return False
        if self.previous_mentions >= 3 and self.mention_change >= max(
            3, self.previous_mentions
        ):
            return True
        return self.net_change <= -3


def compare(
    today: Sequence[ConsumerSignal], previous: Sequence[ConsumerSignal]
) -> List[SignalChange]:
    before = {s.ticker: s for s in previous}
    changes = []
    for signal in today:
        was = before.get(signal.ticker)
        changes.append(SignalChange(
            signal=signal,
            previous_mentions=was.mentions if was else 0,
            previous_net=was.net if was else 0,
        ))
    return changes
