# Adding a source

A source is one module plus one line in the registry. Nothing downstream —
normalisation, matching, clustering, scoring, reporting — needs to know it
exists.

## The contract

```python
class Source:
    name: str

    def fetch(self, context: CollectionContext) -> list[Article]:
        ...
```

Three rules, all of them learned the hard way:

1. **Never raise for an ordinary network problem.** Record the error and
   return what you have. One blocked website must not end a run.
2. **Respect the circuit breaker.** Check `self.should_give_up()` at the top
   of your loop and call `self.note_success()` after a request works. Without
   this, a blocked host costs the full timeout on every single request.
3. **Never claim to have worked.** `outcome()` reports what actually happened;
   `--check-sources` is what turns that into a claim in the README.

## A minimal source

```python
# src/sources/example.py
from typing import Any, Dict, List

from ..models import Article, SourceType
from .base import CollectionContext, HttpClient, Source, SourceError, parse_feed_entries


class ExampleSource(Source):
    name = "example"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.url = str(config.get("url", "https://example.test/feed"))

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        for profile in context.profiles:
            if self.should_give_up():
                break
            self.attempted += 1
            try:
                response = self.client.get(f"{self.url}?symbol={profile.ticker}")
            except SourceError as exc:
                self.record_error(f"{profile.ticker}", exc)
                continue
            self.succeeded += 1
            self.note_success()
            articles.extend(
                parse_feed_entries(
                    response.content,
                    feed_name="Example",
                    collector=self.name,
                    source_type=SourceType.SPECIALIZED_TRADE_PUBLICATION,
                    tickers_hint=[profile.ticker],
                )
            )
            self.client.sleep()
        return articles
```

## Register it

```python
# src/sources/__init__.py
SOURCE_FACTORIES = {
    ...
    "example": lambda config, client, tickers: ExampleSource(
        config.section("sources").get("example", {}), client
    ),
}
```

```yaml
# config.yaml
sources:
  example:
    enabled: true
    url: https://example.test/feed
```

Order matters: sources are polled in registry order, and official sources are
polled first so that when a story is clustered, the exchange filing is already
present and becomes the event's primary source.

## Just an RSS feed?

Then no module is needed at all — add it to `config.yaml`:

```yaml
feeds:
  solar:
    - name: New Solar Trade Weekly
      url: https://example.test/feed
      source_type: specialized_trade_publication
      tickers: [WAAREEENER]      # which companies it serves; omit for all
```

`tickers` is how a run restricted to `--ticker HDFCBANK` avoids polling solar
feeds.

## Source quality

Add the domain so the scorer knows what it is looking at:

```yaml
source_domains:
  example.test: specialized_trade_publication
```

Unlisted domains fall back to `unknown_news_site` (quality 3). Quality feeds
**confidence**, never direction — a tabloid saying something good does not
make it good news, it makes it less certain news.

## Prove it works

```bash
python -m src.main --check-sources
```

Then record the result in the README table. A source is not described as
working until it has appeared as `VERIFIED` on the machine that will run it.

## Test it offline

`tests/test_sources.py` has a `FakeClient`; no test in this repository touches
the network. Cover at least: a successful parse, a failure that is recorded
rather than raised, and the circuit breaker.
