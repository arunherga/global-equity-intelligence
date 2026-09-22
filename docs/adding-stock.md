# Adding a stock

Everything about a company lives in `watchlist.yaml`. No code changes are
needed to add one.

## 1. Add the block

```yaml
tickers:
  NEWTICKER:
    company: New Company Limited
    exchange: [NSE, BSE]
    country: India
    market_segment: MAIN          # or SME
    industry: What it actually does

    aliases:
      - value: New Company Limited
        strength: primary
      - value: NewCo
        strength: secondary
        requires: [widgets, manufacturing]   # context needed for a shorthand
        excludes: [newco holdings]           # a different company
```

Run `pytest` after editing: the watchlist has structural tests.

## 2. Describe the ecosystem, not just the name

This is the part that makes the system worth running. An article about a
competitor's order, a supplier's shutdown or a policy in an export market
should reach this stock even when it never names it.

```yaml
    subsidiaries: [...]     # wholly owned -> counts as DIRECT
    associates:  [...]      # separately listed group companies -> INDIRECT_STRONG
    products:    [...]
    competitors: [...]      # capitalise real names; see below
    customers:   [...]
    suppliers:   [...]
    countries:       [...]
    export_markets:  [...]  # a rule aimed at one of these is INDIRECT_STRONG
    currencies:  [USD/INR]
    regulators:  [SEBI, NSE, BSE, ...]
    government_dependencies: [...]
    sector_topics:        [...]   # domestic industry language
    international_topics: [...]   # the foreign news that never names the company
    macro_topics:         [...]
    technologies:         [...]
```

**Capitalisation is load-bearing.** A capitalised term (`Premier Energies`,
`NTPC`) is read as a named counterparty and can raise a relationship to
`INDIRECT_STRONG`. A lowercase one (`distributors`, `contract farmers`) is
read as a description of a role and is capped at sector level. This is what
stops "a distributor placed an order" being filed as news about a customer.

## 3. Get the commodity roles right

```yaml
    commodities:
      - {name: polysilicon,         role: input}       # price up   -> NEGATIVE
      - {name: solar module prices, role: output}      # price up   -> POSITIVE
      - {name: ferrous scrap,       role: throughput}  # volume matters, not price
```

`role` is what lets one headline — *"prices collapse"* — come out negative for
a producer and positive for a buyer. Getting it wrong inverts the direction
analysis for that commodity, so it is worth a minute's thought.

## 4. Guard a risky alias

If the short name collides with another business, a person or a place, use
`requires` and `excludes`, and add a regression test. `RAVEL` is the worked
example: Ravelcare Limited is not Ravel Electronics.

```yaml
      - value: Ravel
        strength: secondary
        requires: [ravelcare, personal care, haircare, skincare, beauty]
        excludes: [ravel electronics, maurice ravel]
    negative_aliases:
      - Ravel Electronics
```

```python
def test_ravel_electronics_is_not_ravelcare(watchlist, make_article):
    article = make_article("Ravel Electronics bags an LED order in Chennai")
    matches, _ = match_all_entities(article, watchlist.profiles)
    assert not any(m.ticker == "RAVEL" for m in matches)
```

## 5. Add a user shorthand if you want one

```yaml
ticker_aliases:
  NEWCO: NEWTICKER
```

`--ticker newco` then resolves. This is how `HDFC` resolves to `HDFCBANK`.

## 6. Fill in the BSE scrip code

```yaml
    ir:
      website: https://example.com/
      bse_code: "500000"
```

A wrong code silently returns another company's filings, so the collector
verifies the returned company name against the profile's aliases and discards
the batch with a loud diagnostic if it does not match. Leave it blank rather
than guessing.

## 7. Check the queries and the matching

```bash
python -c "
from src.config import load_config
from src.profiles import load_watchlist
from src.query_generator import generate_for_profile
w = load_watchlist(config=load_config())
for q in generate_for_profile(w.get('NEWTICKER')):
    print(f'{q.kind.value:14s} {q.text}')
"

python -m src.main --ticker NEWTICKER --dry-run --verbose
```
