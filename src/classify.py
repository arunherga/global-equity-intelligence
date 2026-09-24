"""Event classification.

Deterministic keyword rules, one category per rule, several categories per
article. Cheap, reviewable, and it gives the same answer twice. The
``Classifier`` protocol exists so an LLM classifier can be swapped in later
without touching anything upstream or downstream.

Matching goes through :mod:`src.matching`, which works on word boundaries. The
sibling project learned this the hard way: with substring matching ``"api"``
matched *capi*tal, ``"cut"`` matched exe*cut*ive and ``"ofs"`` matched pr*ofs*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from .matching import contains_any, contains_phrase, fold
from .models import Article, EventCategory

# Ordered: earlier rules win when a headline triggers several close relatives.
CATEGORY_KEYWORDS: List[Tuple[EventCategory, Tuple[str, ...]]] = [
    (EventCategory.EARNINGS, (
        "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results",
        "net profit", "reports profit", "posts profit", "revenue rose", "revenue fell",
        "earnings", "ebitda", "topline", "bottomline", "profit after tax", "pat rose",
        "financial results", "results beat", "results miss", "quarterly earnings",
    )),
    (EventCategory.GUIDANCE, (
        "guidance", "outlook raised", "outlook cut", "forecast", "targets revenue",
        "expects revenue", "production target", "sees revenue", "projects growth",
    )),
    (EventCategory.ORDER_WIN, (
        "wins order", "bags order", "secures order", "receives order", "order win",
        "new order", "order worth", "letter of intent", "order book", "bags contract",
        "wins contract", "secures contract", "awarded contract", "supply agreement",
        "purchase order", "gw order", "mw order",
    )),
    (EventCategory.ORDER_LOSS, (
        "order cancelled", "order canceled", "loses order", "contract terminated",
        "contract cancelled", "order withdrawn", "loses contract",
    )),
    (EventCategory.CONTRACT_WIN, ("signs mou", "memorandum of understanding", "signs agreement",
                                  "signs contract", "wins tender", "wins bid", "lowest bidder")),
    (EventCategory.CONTRACT_LOSS, ("loses tender", "bid rejected", "disqualified from tender")),
    (EventCategory.NEW_CUSTOMER, ("new customer", "onboards client", "adds client",
                                  "wins client", "first order from")),
    (EventCategory.CUSTOMER_LOSS, ("loses client", "client exits", "customer terminates")),
    (EventCategory.NEW_PLANT, ("new plant", "new factory", "greenfield", "sets up plant",
                               "commissions plant", "breaks ground", "new manufacturing facility")),
    (EventCategory.CAPACITY_EXPANSION, ("capacity expansion", "expands capacity",
                                        "doubles capacity", "additional capacity", "brownfield",
                                        "capacity addition", "ramp up production", "debottleneck")),
    (EventCategory.EXPANSION, ("expansion plan", "expands operations", "enters new market",
                               "new branch", "branch expansion", "opens office")),
    (EventCategory.CAPEX, ("capital expenditure", "capex", "invests rs", "investment of rs",
                           "crore investment", "million investment", "billion investment")),
    (EventCategory.MERGER, ("merger", "merges with", "amalgamation", "scheme of arrangement")),
    (EventCategory.ACQUISITION, ("acquires", "acquisition", "takeover", "buys stake",
                                 "to acquire", "majority stake")),
    (EventCategory.DIVESTITURE, ("divests", "divestment", "sells stake", "hives off",
                                 "sells business", "stake sale")),
    (EventCategory.INVESTMENT, ("invests in", "strategic investment", "funding round")),
    (EventCategory.PARTNERSHIP, ("partnership", "partners with", "joint venture", "tie-up",
                                 "collaboration", "alliance")),
    (EventCategory.NEW_PRODUCT, ("launches", "unveils", "new product", "product launch",
                                 "introduces new")),
    (EventCategory.PRODUCT_DELAY, ("delayed launch", "postpones launch", "delays production")),
    (EventCategory.PRODUCT_FAILURE, ("recall", "product recall", "defect", "faulty")),
    (EventCategory.EXPORT_ORDER, ("export order", "overseas order", "exports to",
                                  "international order", "export contract")),
    (EventCategory.EXPORT_RESTRICTION, ("export ban", "export restriction", "export duty",
                                        "export curb", "export licence", "export rebate")),
    (EventCategory.IMPORT_RESTRICTION, ("import ban", "import restriction", "import curb",
                                        "import duty", "import licence", "quota on imports",
                                        "import alert", "import refusal")),
    (EventCategory.TARIFF, ("tariff", "anti-dumping", "antidumping", "countervailing duty",
                            "customs duty", "safeguard duty", "duty hike", "trade remedy")),
    (EventCategory.TRADE_POLICY, ("trade policy", "trade deal", "free trade agreement",
                                  "trade agreement", "wto", "trade war", "sanctions")),
    (EventCategory.REGULATORY, ("regulator", "regulation", "new rules", "notification",
                                "compliance", "approval granted", "clearance granted",
                                "environmental clearance", "licence granted", "inspection",
                                "warning letter", "form 483", "penalty imposed", "guidelines",
                                "policy framework", "norms")),
    (EventCategory.GOVERNMENT_POLICY, ("government notifies", "notifies", "government policy",
                                       "cabinet approves", "scheme launched",
                                       "budget allocation", "subsidy", "incentive scheme",
                                       "ministry announces", "policy announced", "pli scheme")),
    (EventCategory.TENDER, ("tender", "auction", "e-auction", "bid invited",
                            "request for proposal", "rfp", "bids invited")),
    (EventCategory.LITIGATION, ("lawsuit", "sues", "court case", "tribunal", "arbitration",
                                "nclt", "appeal filed", "class action")),
    (EventCategory.LEGAL, ("legal notice", "injunction", "settlement", "verdict", "judgment")),
    (EventCategory.MANAGEMENT_CHANGE, ("resigns", "steps down", "appoints", "new ceo", "new cfo",
                                       "managing director", "board appoints", "succession",
                                       "elevated to", "takes charge")),
    (EventCategory.PROMOTER_ACTIVITY, ("promoter stake", "promoter pledge", "promoter sells",
                                       "promoter buys", "pledged shares")),
    (EventCategory.INSIDER_BUYING, ("insider buying", "bulk deal buy", "block deal buy")),
    (EventCategory.INSIDER_SELLING, ("insider selling", "bulk deal sell", "block deal sell",
                                     "offloads shares")),
    (EventCategory.SHARE_BUYBACK, ("buyback", "share repurchase")),
    (EventCategory.DIVIDEND, ("dividend", "interim dividend", "final dividend", "record date")),
    (EventCategory.FUNDRAISING, ("raises funds", "qip", "rights issue", "preferential allotment",
                                 "fund raise", "ipo", "fpo", "offer for sale", "ofs")),
    (EventCategory.DEBT, ("debt", "bonds", "ncd", "loan facility", "refinancing",
                          "debt reduction", "borrowing")),
    (EventCategory.CREDIT_RATING, ("credit rating", "rating upgrade", "rating downgrade",
                                   "crisil", "icra", "care ratings", "moody", "fitch",
                                   "s&p global ratings")),
    (EventCategory.SUPPLY_CHAIN, ("supply chain", "shipment delay", "logistics disruption",
                                  "port congestion", "freight rates", "container shortage",
                                  "supply disruption", "shortage")),
    (EventCategory.RAW_MATERIAL, ("raw material", "input cost", "feedstock", "intermediates")),
    (EventCategory.COMMODITY, ("prices rise", "prices fall", "price surge", "price crash",
                               "commodity prices", "spot price", "price index", "glut",
                               "oversupply", "price war")),
    (EventCategory.CYBERSECURITY, ("cyberattack", "data breach", "ransomware", "hacked",
                                   "security incident", "phishing")),
    (EventCategory.FRAUD, ("fraud", "scam", "embezzlement", "forensic audit", "misappropriation")),
    (EventCategory.GEOPOLITICAL, ("geopolitical", "conflict", "war", "strait", "red sea",
                                  "blockade", "diplomatic")),
    (EventCategory.INTEREST_RATE, ("repo rate", "interest rate", "rate cut", "rate hike",
                                   "monetary policy", "federal reserve", "fed cuts",
                                   "fed raises", "policy rate", "crr", "slr")),
    (EventCategory.CURRENCY, ("rupee", "exchange rate", "currency", "forex", "depreciation",
                              "appreciation", "dollar index")),
    (EventCategory.MACRO, ("gdp", "inflation", "cpi", "wpi", "recession", "unemployment",
                           "industrial production", "pmi", "fiscal deficit")),
    (EventCategory.INDUSTRY_DEMAND, ("demand growth", "demand falls", "demand outlook",
                                     "sales volumes", "market growth", "industry growth",
                                     "consumption rises", "consumption falls")),
    (EventCategory.TECHNOLOGY, ("technology", "artificial intelligence", "automation",
                                "breakthrough", "patent", "research and development")),
    (EventCategory.ANALYST_ACTION, ("brokerage", "target price", "upgrades stock",
                                    "downgrades stock", "initiates coverage", "buy rating",
                                    "sell rating", "analysts expect")),
    (EventCategory.COMPETITOR, ("rival", "competitor", "market share", "competing")),
]

# Co-occurrence rules. Headlines rarely use the exact phrase "wins order" —
# they write "bags 1.2 GW export order". A verb from the first set plus a noun
# from the second is the reliable signal.
CATEGORY_COOCCUR: List[Tuple[EventCategory, Tuple[str, ...], Tuple[str, ...]]] = [
    (EventCategory.ORDER_WIN,
     ("wins", "won", "bags", "secures", "secured", "receives", "received", "lands",
      "awarded", "clinches", "gets", "to supply", "supply of", "receipt of",
      "bagged", "announces receipt"),
     ("order", "orders", "contract", "contracts", "work order", "letter of award",
      "letter of intent", "supply agreement", "ppa", "mandate")),
    (EventCategory.ORDER_LOSS,
     ("loses", "lost", "cancelled", "canceled", "terminated", "withdrawn", "scrapped"),
     ("order", "orders", "contract", "contracts", "tender")),
    (EventCategory.CONTRACT_WIN,
     ("wins", "bags", "secures", "awarded", "emerges", "selected"),
     ("tender", "bid", "auction", "concession", "mandate")),
    (EventCategory.CAPACITY_EXPANSION,
     ("expand", "expands", "expanding", "add", "adds", "doubles", "triples", "raise", "raises"),
     ("capacity", "gw", "mw", "tonnes", "mtpa", "production capacity", "output")),
    (EventCategory.EARNINGS,
     ("reports", "posts", "records", "announces", "declares"),
     ("net profit", "profit after tax", "revenue", "nim", "net interest margin",
      "deposit growth", "loan growth", "earnings", "quarterly results",
      "operating margin", "ebitda", "topline")),
    (EventCategory.NEW_PLANT,
     ("commissions", "commissioned", "inaugurates", "opens", "sets up", "starts",
      "begins", "builds", "establishes"),
     ("plant", "factory", "facility", "manufacturing unit", "production line",
      "gigafactory", "refinery", "mill")),
    (EventCategory.MANAGEMENT_CHANGE,
     ("appoints", "appointed", "resigns", "resigned", "quits", "steps down", "names"),
     ("ceo", "cfo", "chairman", "managing director", "director", "chief executive",
      "chief financial officer", "board")),
    (EventCategory.ANALYST_ACTION,
     ("analysts", "brokerage", "brokerages", "should you", "target"),
     ("upside", "downside", "buy", "sell", "hold", "price target", "rating")),
    (EventCategory.REGULATORY,
     ("fda", "usfda", "ema", "edqm", "rbi", "sebi", "cdsco", "regulator"),
     ("approval", "approves", "nod", "inspection", "observations", "warning letter",
      "penalty", "fine", "licence", "certificate", "clearance")),
    (EventCategory.REGULATORY,
     ("tightens", "tighten", "eases", "relaxes", "revises", "imposes", "introduces",
      "sets", "raises", "lowers", "caps", "bans", "mandates", "notifies", "amends"),
     ("limit", "limits", "standard", "standards", "norm", "norms", "rule", "rules",
      "regulation", "regulations", "requirement", "requirements", "threshold",
      "ceiling", "cap", "residue limits", "specification", "policy", "scheme",
      "incentive", "incentives", "duty", "quota")),
    (EventCategory.COMMODITY,
     ("prices", "price", "rates"),
     ("fall", "falls", "fell", "rise", "rises", "rose", "slump", "slumps", "surge",
      "surges", "collapse", "collapses", "crash", "drop", "drops", "jump", "jumps",
      "low", "high", "record")),
    (EventCategory.INDUSTRY_DEMAND,
     ("demand", "shipments", "installations", "volumes", "consumption"),
     ("rise", "rises", "fall", "falls", "grow", "grows", "growth", "decline",
      "declines", "record", "forecast", "outlook", "slow", "slows")),
]

# Language that should pull a score down rather than add a category.
SPECULATIVE_MARKERS = (
    "may", "might", "could", "reportedly", "rumour", "rumor", "speculation",
    "sources say", "people familiar", "is said to", "likely to", "plans to",
    "in talks", "considering", "exploring", "mulls", "weighing",
)

OPINION_MARKERS = (
    "opinion", "editorial", "column", "viewpoint", "analysis:", "explained",
    "here's why", "what it means for investors", "should you buy",
    "stocks to watch", "stocks to buy", "top picks", "multibagger",
    "technical view", "chart of the day", "market outlook today",
)

# Scheduled statistical publications. A regulator feed is high quality, which
# is exactly why these score well on source alone - but "Money Market
# Operations as on 21 September" is a data release, not a development. The
# live backfill had seven of these clearing the reporting threshold, two of
# them at 8 and 10 out of 15.
ROUTINE_RELEASE_MARKERS = (
    "money market operations", "variable rate reverse repo", "vrrr auction",
    "auction under laf", "under laf on", "data quality index",
    "forex inflows via", "weekly statistical supplement",
    "scheduled banks' statement", "reserve money", "lending and deposit rates",
    "sectoral deployment of bank credit", "daily liquidity operations",
    "result of the auction", "auction result", "treasury bill auction",
    "monthly bulletin", "statistical tables", "provisional figures",
    "list of", "index numbers",
)

# Market commentary: what the index did, or might do next week. None of it is
# news about a company.
MARKET_CHATTER_MARKERS = (
    "stock market this week", "stock market next week", "week ahead",
    "markets this week", "market outlook today", "closing bell", "market wrap",
    "sensex", "nifty", "top gainers", "top losers", "stocks to watch",
    "buzzing stocks", "trade setup", "technical view", "f&o cues",
    "gainers and losers", "market live", "opening bell", "share price target",
)

OFFICIAL_MARKERS = (
    "announces", "announcement", "intimation", "disclosure", "board meeting",
    "outcome of board meeting", "press release", "regulation 30", "filing",
)


@dataclass
class Classification:
    """Categories plus the qualitative flags the scorer needs."""

    categories: List[EventCategory] = field(default_factory=list)
    matched_terms: Dict[str, str] = field(default_factory=dict)
    speculative: bool = False
    opinion: bool = False
    official_language: bool = False
    routine_release: bool = False
    market_chatter: bool = False

    @property
    def primary(self) -> EventCategory:
        return self.categories[0] if self.categories else EventCategory.OTHER

    def has(self, *categories: EventCategory) -> bool:
        return any(c in self.categories for c in categories)


class Classifier(Protocol):  # pragma: no cover - interface
    def classify(self, article: Article) -> Classification: ...


class RuleClassifier:
    """Keyword classifier. Replaceable; see :class:`Classifier`."""

    def __init__(self, max_categories: int = 4) -> None:
        self.max_categories = max_categories

    def classify(self, article: Article) -> Classification:
        text = fold(article.text)
        headline = fold(article.title)

        categories: List[EventCategory] = []
        matched: Dict[str, str] = {}
        for category, keywords in CATEGORY_KEYWORDS:
            if category in categories:
                continue
            for keyword in keywords:
                if contains_phrase(text, keyword):
                    categories.append(category)
                    matched[category.value] = keyword
                    break

        for category, verbs, nouns in CATEGORY_COOCCUR:
            if category in categories:
                continue
            verb = next((v for v in verbs if contains_phrase(text, v)), None)
            noun = next((n for n in nouns if contains_phrase(text, n)), None)
            if verb and noun:
                categories.append(category)
                matched[category.value] = f"{verb}+{noun}"

        # Headline evidence outranks body evidence.
        def _in_headline(category: EventCategory) -> bool:
            term = matched.get(category.value, "")
            parts = term.split("+") if "+" in term else [term]
            return all(contains_phrase(headline, p) for p in parts if p)

        categories.sort(key=lambda c: 0 if _in_headline(c) else 1)
        trimmed = categories[: self.max_categories]

        if not trimmed:
            trimmed = [EventCategory.OTHER]

        return Classification(
            categories=trimmed,
            matched_terms={c.value: matched.get(c.value, "") for c in trimmed},
            speculative=contains_any(headline, SPECULATIVE_MARKERS),
            opinion=contains_any(headline, OPINION_MARKERS),
            official_language=article.is_official or contains_any(text, OFFICIAL_MARKERS),
            routine_release=contains_any(headline, ROUTINE_RELEASE_MARKERS),
            market_chatter=contains_any(headline, MARKET_CHATTER_MARKERS),
        )


DEFAULT_CLASSIFIER = RuleClassifier()


def classify_article(article: Article, classifier: Optional[Classifier] = None) -> Classification:
    return (classifier or DEFAULT_CLASSIFIER).classify(article)
