from datetime import datetime, timedelta, timezone

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import Query
from jobpipe.rotation import ALL_TERMS, catalogue, seed_rows, select_queries
from jobpipe.runtime import seed_queries

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def query(
    identifier: int,
    *,
    days_ago: int | None = None,
    cooldown: int = 14,
    runs: int = 0,
    total: int = 0,
    enabled: bool = True,
) -> Query:
    return Query(
        id=identifier,
        source="adzuna",
        terms=f"term {identifier}",
        cooldown_days=cooldown,
        last_run_at=None if days_ago is None else NOW - timedelta(days=days_ago),
        runs=runs,
        yield_total=total,
        enabled=enabled,
    )


def ids(queries: list[Query]) -> list[int]:
    return [q.id for q in queries]


def test_never_run_queries_come_first_then_the_oldest_expired() -> None:
    pool = [query(1, days_ago=20), query(2, days_ago=40), query(3), query(4, days_ago=30)]
    assert ids(select_queries(pool, NOW, 4)) == [3, 2, 4, 1]
    assert ids(select_queries(pool, NOW, 2)) == [3, 2]


def test_a_query_inside_its_cooldown_is_not_selected() -> None:
    pool = [query(1, days_ago=3), query(2, days_ago=14), query(3, days_ago=13)]
    assert ids(select_queries(pool, NOW, 5)) == [2]


def test_the_cooldown_is_configurable_per_query() -> None:
    pool = [query(1, days_ago=5, cooldown=3), query(2, days_ago=5, cooldown=7)]
    assert ids(select_queries(pool, NOW, 5)) == [1]


def test_when_nothing_is_eligible_the_best_yielding_queries_are_replayed() -> None:
    pool = [
        query(1, days_ago=2, runs=4, total=2),
        query(2, days_ago=1, runs=2, total=9),
        query(3, days_ago=3, runs=1, total=3),
        query(4, days_ago=5, runs=0),
    ]
    assert ids(select_queries(pool, NOW, 2)) == [2, 3]
    assert ids(select_queries(pool, NOW, 10)) == [2, 3, 1, 4]


def test_disabled_queries_are_never_selected() -> None:
    pool = [query(1, enabled=False), query(2, days_ago=1, enabled=False)]
    assert select_queries(pool, NOW, 3) == []
    assert ids(select_queries([query(1, enabled=False), query(2)], NOW, 3)) == [2]


def test_the_catalogue_is_seeded_from_the_taxonomy_in_three_languages(config: AppConfig, store: Store) -> None:
    terms = config.taxonomy.query_terms()
    assert set(config.taxonomy.queries) == {"en", "fr", "nl"}
    assert "ingénieur données junior" in terms and "junior data-ingenieur" in terms and "junior data engineer" in terms
    added = seed_queries(store, config)
    assert added == 2 * len(terms) + 1
    assert seed_queries(store, config) == 0
    assert [q.terms for q in store.list_queries("arbeitnow")] == [ALL_TERMS]
    adzuna = store.list_queries("adzuna")
    assert [q.terms for q in adzuna] == terms
    assert all(q.cooldown_days == config.settings.pipeline.default_cooldown_days for q in adzuna)


def test_recording_a_run_updates_the_rotation_state(config: AppConfig, store: Store) -> None:
    seed_queries(store, config)
    first = store.list_queries("adzuna")[0]
    store.record_query_run(first.id, 4)
    store.record_query_run(first.id, 0)
    updated = store.list_queries("adzuna")[0]
    assert updated.runs == 2 and updated.yield_total == 4 and updated.last_run_at is not None
    chosen = select_queries(store.list_queries("adzuna"), datetime.now(timezone.utc), 3)
    assert first.id not in ids(chosen)


def test_a_dry_run_catalogue_falls_back_to_the_seed_without_a_database_row(config: AppConfig, store: Store) -> None:
    pool = catalogue(store, config, "adzuna", "terms")
    assert len(pool) == len(config.taxonomy.query_terms()) and all(q.id == 0 for q in pool)
    assert [r[1] for r in seed_rows(config, "arbeitnow", "all")] == [ALL_TERMS]
