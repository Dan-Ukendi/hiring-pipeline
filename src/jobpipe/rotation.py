from datetime import datetime, timedelta

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import Query

ALL_TERMS = "*"


def is_eligible(query: Query, now: datetime) -> bool:
    if query.last_run_at is None:
        return True
    return query.last_run_at + timedelta(days=query.cooldown_days) <= now


def yield_rate(query: Query) -> float:
    return query.yield_total / query.runs if query.runs else 0.0


def select_queries(queries: list[Query], now: datetime, limit: int) -> list[Query]:
    eligible = [q for q in queries if q.enabled and is_eligible(q, now)]
    if eligible:
        eligible.sort(key=lambda q: (q.last_run_at is not None, q.last_run_at or now, q.id))
        return eligible[:limit]
    replay = [q for q in queries if q.enabled]
    replay.sort(key=lambda q: (-yield_rate(q), q.last_run_at or now, q.id))
    return replay[:limit]


def seed_rows(config: AppConfig, source: str, query_mode: str) -> list[tuple[str, str, dict[str, object], int]]:
    cooldown = config.settings.pipeline.default_cooldown_days
    terms = [ALL_TERMS] if query_mode == "all" else config.taxonomy.query_terms()
    return [(source, term, {}, cooldown) for term in terms]


def catalogue(store: Store, config: AppConfig, source: str, query_mode: str) -> list[Query]:
    stored = store.list_queries(source)
    if stored:
        return stored
    return [
        Query(id=0, source=row[0], terms=row[1], params=row[2], cooldown_days=row[3])
        for row in seed_rows(config, source, query_mode)
    ]
