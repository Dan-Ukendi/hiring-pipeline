import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobpipe.config import AppConfig
from jobpipe.models import ProbeOutcome
from jobpipe.probe.report import write_report
from jobpipe.runtime import open_runtime
from jobpipe.sources.registry import SourceRegistry


@dataclass
class ProbeRunResult:
    outcomes: list[ProbeOutcome]
    report_path: Path


def _write_sample(config: AppConfig, outcome: ProbeOutcome) -> str:
    directory = config.reports_dir / "samples"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = outcome.probed_at.strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"probe-{outcome.source}-{stamp}.json"
    manifest = {
        "source": outcome.source,
        "probed_at": outcome.probed_at.isoformat(),
        "samples": [s.model_dump() for s in outcome.samples],
    }
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return target.relative_to(config.root).as_posix()


def run_probe(
    config: AppConfig, names: list[str], logger: logging.Logger, transport: Any = None
) -> ProbeRunResult:
    outcomes: list[ProbeOutcome] = []
    with open_runtime(config, logger, transport) as rt:
        registry = SourceRegistry(config, rt.store)
        for name in names:
            source = registry.create(name, rt.http, logger, gated=False)
            logger.info("probing %s", name)
            outcome = source.probe()
            outcome = outcome.model_copy(update={"sample_path": _write_sample(config, outcome)})
            outcomes.append(rt.store.add_probe_result(outcome))
    report_path = write_report(config, outcomes)
    return ProbeRunResult(outcomes=outcomes, report_path=report_path)
