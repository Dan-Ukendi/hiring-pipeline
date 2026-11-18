import importlib

_MODULES = (
    "jobpipe.sources.ats.greenhouse",
    "jobpipe.sources.ats.lever",
    "jobpipe.sources.ats.ashby",
    "jobpipe.sources.ats.smartrecruiters",
    "jobpipe.sources.ats.recruitee",
    "jobpipe.sources.ats.workable",
    "jobpipe.sources.ats.personio",
    "jobpipe.sources.aggregators.adzuna",
    "jobpipe.sources.aggregators.arbeitnow",
    "jobpipe.sources.aggregators.actiris",
)


def load_sources() -> None:
    for module in _MODULES:
        importlib.import_module(module)
