"""Add one posting, named by its URL, to the database so a CV can be made for it.

This is how you apply to any job, whatever its field: the discovery filters (AI/data domain,
junior level, Brussels) are bypassed because you chose the offer yourself.
"""

from jobpipe.config import AppConfig
from jobpipe.extract.normalize import GENERIC_SOURCE, extract_document, page_identity
from jobpipe.http.client import HTML_ACCEPT, HttpTransportError
from jobpipe.models import Discovery, JobPosting, RawDocument
from jobpipe.runtime import Runtime


class AddJobError(Exception):
    pass


def add_posting(rt: Runtime, app_config: AppConfig, url: str) -> JobPosting:
    """Fetch ``url``, extract the posting and store it. Raises AddJobError with a readable reason."""
    try:
        page, _ = rt.http.get_following_redirects(url, accept=HTML_ACCEPT)
    except HttpTransportError as exc:
        raise AddJobError(f"could not fetch {url}: {exc}") from exc
    if not page.ok:
        raise AddJobError(f"could not fetch {url}: HTTP {page.status_code}")
    raw = RawDocument(
        discovery=Discovery(source=GENERIC_SOURCE, native_id=page_identity(url), url=url),
        source_url=page.url,
        content=page.content,
        content_type=page.content_type,
        artifact_sha256=page.sha256,
    )
    outcome = extract_document(raw)
    if outcome.posting is None:
        detail = outcome.failure.detail if outcome.failure else "unknown reason"
        raise AddJobError(
            f"could not read a job posting at {url} ({detail}). Open the offer's own page "
            "(not a search-results list) and use that address."
        )
    run = rt.store.start_run(None, app_config.config_hash, f"add {url}")
    try:
        rt.store.upsert_job(outcome.posting, run.id)
    except Exception:
        rt.store.finish_run(run.id, "failed")
        raise
    rt.store.finish_run(run.id, "ok")
    return outcome.posting
