# Global Equity Intelligence

A personal intelligence agent for ten Indian equities. It runs twice a day,
reads far more than the ticker names, and publishes one Markdown briefing to
this repository that answers a single question each morning: *is there
anything here I should act on today?*

**It is not a news reader.** Searching Google News for "MSTC", "Waaree" and
"Coal India" finds the easy 20% of what matters. The other 80% never mentions
the company: Chinese module prices, EU pesticide residue limits, Newcastle
coal, an API plant shutdown in Zhejiang, a Salesforce AI announcement. This
system models the business ecosystem around each stock and matches on
*exposure*, not just on names.

- Latest briefing: [`reports/`](reports/)
- What the output looks like: [`docs/sample-report.md`](docs/sample-report.md)
  — **fabricated articles from fictional outlets**, used to show the report's
  shape and to exercise the pipeline offline. Not news.
- How it works: [`docs/architecture.md`](docs/architecture.md)

**Free-first by design.** No paid APIs, no API keys, no quotas. Public RSS,
exchange disclosures, regulator feeds, government pages and deterministic
Python. The optional AI layer is off by default and the agent is fully
functional without it.

## The watchlist

| Ticker | Company | Exchange | Why it needs more than a name search |
| --- | --- | --- | --- |
| `MSTCLTD` | MSTC Limited | NSE, BSE | Government e-auctions, coal and mineral block auctions, vehicle scrappage policy, ferrous scrap prices |
| `WAAREEENER` | Waaree Energies Limited | NSE, BSE | Chinese module and polysilicon prices, US tariffs and AD/CVD, ALMM, PLI, global solar demand |
| `FRESHARA` | Freshara Agro Exports Limited | NSE (SME) | EU and US food rules, pesticide residue limits, gherkin crops, freight rates, EUR/INR |
| `JKIPL` | Jinkushal Industries Limited | NSE, BSE | Infrastructure capex, mining activity, used-equipment markets, Caterpillar and Komatsu demand signals |
| `RAVEL` | Ravelcare Limited | BSE (SME) | D2C beauty, quick commerce, acquisition costs, packaging and ingredient inputs |
| `COALINDIA` | Coal India Limited | NSE, BSE | Power demand, e-auction premiums, rail rakes, global thermal coal, Ministry of Coal policy |
| `KSOLVES` | Ksolves India Limited | NSE, BSE | US enterprise IT budgets, the Salesforce and Odoo ecosystems, AI spending, USD/INR |
| `TMB` | Tamilnad Mercantile Bank Limited | NSE, BSE | RBI policy and actions, MSME and textile credit in Tamil Nadu, deposit competition |
| `SUPRIYA` | Supriya Lifescience Limited | NSE, BSE | USFDA and EDQM actions, Chinese API supply, intermediate prices, export markets |
| `HDFCBANK` | HDFC Bank Limited | NSE, BSE | RBI policy, deposit growth, credit quality, foreign flows |

`HDFC` normalises to `HDFCBANK`. The former HDFC Limited merged into the bank
and is **not** treated as a separate listed equity; HDFC Life, HDFC AMC and
HDFC Ergo are treated as group companies (`INDIRECT_STRONG`), not as HDFC Bank
itself.

## What it produces

```
reports/2026-09-22.md          the briefing
data/daily/2026-09-22.json     the same run, structured
data/events/2026/WAAREEENER/   one JSON file per event, kept forever
data/seen_articles.json        so the evening run adds instead of repeating
```

The report opens with **what needs attention**, then a watchlist dashboard,
then **global events affecting my stocks** (the foreign news that never names
them), then cross-stock events, then one section per company, then **run
diagnostics** — which sources actually worked today.

Every finding carries its own reasoning:

```
WAAREEENER
Impact 11/15   Direction POSITIVE   Confidence 91%   Relationship DIRECT

Score reasons
- +5 Direct company event
- +4 Major order or contract
- +3 Official exchange announcement
- +2 4 independent sources
- +2 Company appears in the headline
```

## Events, not articles

A Reuters story, an Economic Times write-up, an NSE filing and the company's
press release about one export order are **one event with four sources**, not
four intelligence items. The number of *independent* sources is then a scoring
signal, the exchange filing becomes the primary source, and the event can keep
evolving: a rumour on day 1, board approval on day 3 and an environmental
clearance on day 20 update the same record, with `first_seen`, `last_updated`
and a full `event_history`.

## Exposure matching

Each stock declares its subsidiaries, group companies, products, competitors,
customers, suppliers, commodities (with an **input / output / throughput**
role), currencies, regulators, export markets, sector topics, macro topics and
international topics. An event is matched through any of them, and the link is
labelled:

`DIRECT` · `INDIRECT_STRONG` · `INDIRECT` · `SECTOR` · `MACRO` · `WEAK`

```
Coal India raises production guidance        -> COALINDIA   DIRECT
China reduces solar export rebates           -> WAAREEENER  INDIRECT_STRONG
Newcastle thermal coal collapses             -> COALINDIA   INDIRECT_STRONG
EU tightens pesticide residue limits         -> FRESHARA    INDIRECT_STRONG
RBI cuts the repo rate                       -> TMB, HDFCBANK  INDIRECT
US Federal Reserve changes rates             -> HDFCBANK    MACRO
```

The commodity role is what makes direction work: polysilicon going up is
negative for Waaree (an input), coal going down is negative for Coal India (an
output). `WEAK` links are kept out of the report unless the impact score
reaches 9.

## Impact, direction and confidence are separate

Importance is not a view. `impact_score` (0-15) says how much attention a
development deserves; `direction` says which way it points for *this* company;
`confidence` says how much to trust the read. A ₹3,000 crore new factory is
high impact and honestly `UNCERTAIN` — growth, but also capital intensity and
execution risk. A rate cut is `MIXED` for a bank.

| Band | Score | Meaning |
| --- | --- | --- |
| Noise | 0-2 | Ignore |
| Low | 3-4 | Filed, not reported |
| Relevant | 5-7 | In the report |
| High | 8-10 | In *what needs attention* |
| Very high | 11-12 | Read it today |
| Critical | 13-15 | Read it now |

Nothing in this repository produces a buy, sell or hold view. The dashboard
counts events; it is not a ranking.

## Sources

| Source | Module | Notes |
| --- | --- | --- |
| NSE announcements | `src/sources/nse.py` | Needs a session cookie from the website first. NSE frequently refuses datacentre IPs, including GitHub Actions runners. |
| BSE announcements | `src/sources/bse.py` | Keyed on a numeric scrip code from `watchlist.yaml`. The returned company name is verified against the profile, because a wrong code silently returns someone else's filings. |
| Company IR pages | `src/sources/company_ir.py` | Prefers the feed a site advertises; otherwise extracts announcement-like links. |
| Regulators | `src/sources/regulators.py` | RBI, SEBI, US FDA, EMA, CDSCO, APEDA, DGFT. Only the ones the current watchlist actually needs are polled. |
| Government | `src/sources/government.py` | Coal, Power, MNRE, Steel, Commerce, PIB listing pages. |
| Google News RSS | `src/sources/google_news.py` | One request per generated query, `en-IN`/`IN`, windowed at the source. Carries most of the coverage. |
| GDELT | `src/sources/gdelt.py` | International exposure topics only — foreign coverage an India-locale search under-reports. |
| Sector & press RSS | `src/sources/rss.py` | Configured in `config.yaml`; a moved feed is rediscovered from the site's `<head>`. |

**No source in this table has been verified on a live network yet.** The
environment this project was built in blocks all outbound HTTP except package
registries, so every host above returned `403` from the egress proxy. That is
recorded rather than papered over:

```bash
python -m src.main --check-sources
```

prints a VERIFIED / FAILED table. Run it once on the machine that will run the
agent and paste the result here. Until then, treat the table above as
*configured*, not *working*. One source failing never stops a run — failures
land in the report's Run Diagnostics section, and a host that fails five times
in a row is abandoned for the rest of the run rather than costing a timeout on
every request.

## Query generation

Queries are derived from the profiles, not hand-maintained. Roughly 26 per
company, budgeted across company, sector, international, commodity,
regulatory, competitor, customer and supplier kinds, then de-duplicated
globally — both banks want `RBI monetary policy`, so it is searched once and
matching decides afterwards who it touches.

```
"Waaree Energies"              polysilicon price
"Waaree Energies" order        China solar module prices fall
India solar ALMM policy        solar module prices
US solar tariff India imports  module oversupply
```

## Usage

```bash
pip install -r requirements.txt

python -m src.main                      # today's run
python -m src.main --dry-run --verbose  # run fully, write nothing
python -m src.main --ticker WAAREEENER
python -m src.main --ticker WAAREEENER,SUPRIYA,COALINDIA

# historical backfill, in slices (news search caps results per query)
python -m src.main --backfill-days 365 --slice-days 14
python -m src.main --ticker WAAREEENER --backfill-days 730 --slice-days 14

# prove which sources work on this machine
python -m src.main --check-sources

# ask the event database a question
python -m src.main --query WAAREEENER --categories TARIFF
python -m src.main --query SUPRIYA --categories SUPPLY_CHAIN,RAW_MATERIAL
python -m src.main --query COALINDIA --min-impact 10
```

The historical store is the point of keeping every event: *how did WAAREEENER
react to tariff news?*, *what happened to SUPRIYA each time Chinese API supply
was disrupted?* Each event reserves `price_at_event`, `return_1d` through
`return_60d`, `volume_change` and `gap_percentage`, ready for a market-data
integration (Upstox, Kite, yfinance) without a schema change.

## Automation

`.github/workflows/daily-intelligence.yml` runs at **02:37 UTC (08:07 IST)**
and **12:43 UTC (18:13 IST)** — deliberately odd minutes, because GitHub's
scheduler is congested on the hour and runs get delayed or dropped. The
evening run **merges into** the day's report rather than overwriting it: the
run regenerates the report from the union of the day's events, which is far
safer than editing Markdown. Reports and data are committed only when
something changed.

`.github/workflows/tests.yml` runs the offline suite on 3.11 and 3.12 and
fails if `docs/sample-report.md` is out of date.

## Optional AI

Off by default. When enabled it is asked only to *explain* events the
deterministic layer has already flagged — high impact, critical, or complex
indirect — and it is required to answer in JSON. It never scores, never ranks,
and never recommends: buy/sell/hold language is stripped from its output
before the output can reach a report.

```yaml
ai:
  enabled: false
  provider: ollama        # local and free; or openai
  model: llama3.1:8b
  min_impact_score: 9
  max_events_per_run: 12
```

Credentials are read from the environment (`OPENAI_API_KEY`), never from a
configuration file.

## Alerts

The interface exists; only the console channel is implemented. Telegram,
email, Slack, Discord and mobile push are one class and one registry entry
each — see `src/alerts/`.

```yaml
alerts:
  enabled: false
  min_impact_score: 11
  alert_on_official_filing: true
  channels: []
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

284 tests, all offline. The interesting ones are regressions:

- Ravelcare Limited is not Ravel Electronics, and `Ravel` alone needs
  personal-care context
- Supriya Lifescience is not Supriya Sule
- HDFC Life is not HDFC Bank; `HDFC` normalises to `HDFCBANK`
- `"api"` must not match *capital*, `"cut"` must not match *exe**cut**ive*,
  `"ban"` must not match *ur**ban*** — all matching goes through
  `src/matching.py`, which works on word boundaries
- four articles about one order become one event with four sources
- cross-publisher copies are **not** deduplicated, because "three or more
  independent sources" is a scoring signal
- nothing without the company named can be labelled `DIRECT`
- polysilicon up is negative for Waaree; coal down is negative for Coal India
- an evening run adds to the morning's report instead of erasing it
- a blocked host is abandoned after five failures instead of timing out 26 times

## Limitations

- **No source is verified.** See the Sources section. Run `--check-sources` on
  your own machine.
- **Google News links are opaque.** They are resolved late and only for
  articles that can still reach the report; unresolvable ones are left as-is
  and reported, not silently replaced.
- **Classification is keyword-based.** It is cheap, reviewable and
  reproducible, and it will miss unusual phrasings. The `Classifier` protocol
  exists so an LLM can replace it without touching anything else.
- **Direction is a heuristic read of headlines**, not of financial statements.
  It is deliberately willing to answer `UNCERTAIN`.
- **Profiles decay.** Competitors, customers and products change. Enrichment
  refreshes `data/profiles/` every 30 days, but `watchlist.yaml` is yours to
  maintain — and enrichment will never overwrite it.
- **BSE scrip codes for JKIPL, RAVEL and FRESHARA are not filled in**, so BSE
  is skipped for those three with a note until you add them.
- **No price data yet.** The fields are reserved; nothing populates them.
- **SME coverage is thin.** FRESHARA and RAVEL are covered mostly by
  lower-quality outlets, which the scorer penalises — by design, but it does
  mean fewer items clear the threshold for those two.

## Licence

Personal project. No warranty. Nothing here is investment advice.
