from jobpipe.apply.vendors import greenhouse
from jobpipe.apply.vendors.base import FormSpecFetcher

FORM_SPEC_FETCHERS: dict[str, FormSpecFetcher] = {"greenhouse": greenhouse.fetch_form_spec}
