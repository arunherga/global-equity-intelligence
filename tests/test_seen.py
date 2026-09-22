"""The seen-article store."""

from __future__ import annotations

from datetime import date, timedelta

from src.seen import SeenStore


def test_marks_and_recognises(tmp_path, make_article):
    store = SeenStore(tmp_path / "seen.json").load()
    article = make_article("Waaree bags an order")
    assert article.article_id not in store
    store.mark(article, when=date(2026, 9, 22))
    assert article.article_id in store


def test_split_separates_fresh_from_seen(tmp_path, make_article):
    store = SeenStore(tmp_path / "seen.json").load()
    first = make_article("Waaree bags an order")
    second = make_article("Coal India output rises")
    store.mark(first, when=date(2026, 9, 22))
    fresh, old = store.split([first, second])
    assert [a.title for a in fresh] == ["Coal India output rises"]
    assert [a.title for a in old] == ["Waaree bags an order"]


def test_survives_a_round_trip(tmp_path, make_article):
    path = tmp_path / "seen.json"
    store = SeenStore(path).load()
    article = make_article("Waaree bags an order")
    store.mark(article, when=date(2026, 9, 22))
    store.save()
    assert article.article_id in SeenStore(path).load()


def test_old_entries_are_pruned(tmp_path, make_article):
    store = SeenStore(tmp_path / "seen.json", retention_days=30).load()
    old = make_article("ancient news")
    recent = make_article("todays news")
    store.mark(old, when=date(2026, 1, 1))
    store.mark(recent, when=date(2026, 9, 22))
    removed = store.prune(today=date(2026, 9, 22))
    assert removed == 1
    assert recent.article_id in store
    assert old.article_id not in store


def test_a_corrupt_store_does_not_break_a_run(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SeenStore(path).load()
    assert len(store) == 0
