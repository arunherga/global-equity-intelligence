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

Verified against a live 5-day backfill run on GitHub Actions, 22 September
2026: **3,761 articles scanned, 2,696 unique, 757 events, 171 relevant,
31 high-impact, 3 critical, 110 international.**

| Source | Module | Live status (2026-09-22, GitHub runner) |
| --- | --- | --- |
| NSE announcements | `src/sources/nse.py` | **FAILED** — `www.nseindia.com` read-timed-out from the runner. Exactly the datacentre-IP blocking anticipated. Works from an Indian residential connection; left enabled for local runs. |
| BSE announcements | `src/sources/bse.py` | **FAILED** — 5 attempts, all refused; circuit breaker stopped it after 5. JKIPL and RAVEL were skipped for a missing `bse_code`, as designed. |
| Company IR pages | `src/sources/company_ir.py` | **FIXED 2026-09-23** — seven of ten investor URLs were dead, most of them *silently* (soft 404s served as normal pages, or a redirect to the homepage). URLs re-verified in a browser; the collector now detects a soft 404, discovers the investor section from the site root, follows announcement sub-pages, and recovers titles from filenames. |
| Regulators | `src/sources/regulators.py` | **VERIFIED** — 4 of 7 endpoints, 66 items. APEDA's URL has been corrected to `/announcements`. DGFT's URL was already right: its 403 is datacentre-IP blocking, like NSE and BSE. |
| Government | `src/sources/government.py` | **VERIFIED** — 1 of 6 pages, 8 items, now re-pointed. Coal, Power, MNRE and Steel all confirmed working; Commerce dropped (hash-route SPA, nothing to parse) and the generic PIB feed dropped (defaults to Hindi; ministry pages already carry their PIB releases). |
| Google News RSS | `src/sources/google_news.py` | **VERIFIED** — 251 of 251 queries succeeded, 3,447 articles in 234s. Carries the overwhelming majority of coverage. |
| GDELT | `src/sources/gdelt.py` | **FAILED, now disabled** — 6 queries, 154 seconds, zero articles; answered with a non-JSON error page, then refused. Flip `sources.gdelt.enabled` to retry. |
| Sector & press RSS | `src/sources/rss.py` | **VERIFIED** — 14 of 16 feeds, 240 items. Business Standard and SolarQuarter returned 403 from the runner on every attempt and have been removed. |

Four of the eight sources worked in that run. NSE and BSE block datacentre
IPs (NSE works from an Indian residential connection, so both are left enabled
for local runs) and GDELT returned nothing at all and is now disabled.

The remaining failures were not parser bugs — they were dead URLs, and every
one of them has since been opened in a browser and corrected. Seven of the ten
investor-relations URLs were wrong, and the dangerous part was how quietly
they failed: MSTC's `/Investors.aspx` and Ksolves' `/investor-relations` both
answer with a normal-looking page carrying full site navigation, and Waaree's
`/investors/` redirects to the homepage. All three were logged as "reachable
but nothing parseable", which reads like a broken parser. The collector now
treats a not-found page as a failure and says so.

Re-check at any time, on whatever machine will run the agent:

```bash
python -m src.main --check-sources
```

One source failing never stops a run. Failures land in the report's Run
Diagnostics section, and a host that fails five times in a row is abandoned
for the rest of the run rather than costing a timeout on every request — which
is what kept the BSE and GDELT failures above to seconds rather than minutes.

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

`.github/workflows/daily-intelligence.yml` runs at **06:18 UTC (11:48 IST)**
and **13:14 UTC (18:44 IST)** — deliberately odd minutes, because GitHub's
scheduler is congested on the hour and runs there get delayed or dropped.

GitHub runs `schedule` triggers on a best-effort basis: a newly added schedule
routinely misses its first tick or two, and runs are dropped entirely under
load. `workflow_dispatch` is always reliable, so run the workflow by hand from
the Actions tab if a scheduled run does not appear. The
evening run **merges into** the day's report rather than overwriting it: the
run regenerates the report from the union of the day's events, which is far
safer than editing Markdown. Reports and data are committed only when
something changed.

### GitHub's scheduler has never fired for this repository

Recorded, not guessed. Over the first two days: **7 workflow runs, none with
`event: schedule`.** `push` and `workflow_dispatch` both worked throughout, so
Actions itself is fine — only scheduled delivery is missing. The retry windows
above were added after three misses and then missed twice more, 8 and 38
minutes after they were deployed. Five slots, zero deliveries.

The repository is not a fork, not archived, not disabled, is public, and the
workflow is on the default branch — so none of the usual structural causes
apply. The remaining likely cause is account-level: GitHub silently withholds
scheduled Actions from accounts with an **unverified primary email address**,
which produces exactly this signature. Worth checking
`github.com/settings/emails` first, because if that is it, the fix is free.

### Triggering it from Windows instead

Until scheduled delivery works, `scripts/` drives the workflow from your own
machine through the `workflow_dispatch` API, which has been reliable:

```powershell
# once: give the script a token (fine-grained, Actions -> Read and write)
setx GEI_GITHUB_TOKEN "github_pat_..."      # or just: gh auth login

# once: register the two daily tasks
.\scripts\install-scheduled-task.ps1

# check
Get-ScheduledTask -TaskPath "\GlobalEquityIntelligence\"
Start-ScheduledTask -TaskName gei-midday -TaskPath "\GlobalEquityIntelligence\"
```

The token stays on your machine — it is read from the environment or from the
GitHub CLI, never passed on the command line and never written to the log
(`%LOCALAPPDATA%\global-equity-intelligence\trigger.log`).

The tasks are registered with `-StartWhenAvailable`, so a slot missed because
the machine was asleep runs as soon as it is next available. That is strictly
better than GitHub cron, which drops a missed tick and never revisits it. The
trade-off is that it only fires when your machine is on; the workflow's own
retry windows stay in place to cover the days it is not, in case scheduled
delivery ever starts working.

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

- **NSE, BSE and DGFT block datacentre IPs**, so they fail from GitHub Actions
  and work from an Indian residential connection. They are left enabled; the
  circuit breaker keeps the cost to seconds.
- **Google News carries most of the coverage**, which is a single point of
  failure. GDELT was the intended second leg and does not work.
- **The corrected URLs have not yet been through a live GitHub run.** They were
  verified in a browser on 2026-09-23; the next scheduled run is the test.
- **Jinkushal has no investor section** on its website, and Freshara's domain
  does not resolve. Both rely on exchange announcements alone, and for
  Freshara that means NSE — which is the source that fails from Actions.
- **Filename-derived titles are only as good as the filename.** `receiptoforder`
  becomes "Receipt of order", but a file named `16-09-2026a-wn.pdf` carries
  nothing to recover and is skipped.
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
- **No price data yet.** The fields are reserved; nothing populates them.
- **SME coverage is thin.** FRESHARA and RAVEL are covered mostly by
  lower-quality outlets, which the scorer penalises — by design, but it does
  mean fewer items clear the threshold for those two.

## Licence

Personal project. No warranty. Nothing here is investment advice.
