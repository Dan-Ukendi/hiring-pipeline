import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from jobpipe.contract.clock import Clock
from jobpipe.contract.enums import RunPhase, RunStatus
from jobpipe.db.run_ledger import RunLedger


@dataclass(frozen=True)
class Step:
    name: str
    phase: RunPhase
    action: Callable[[int], str]


@dataclass
class ChainResult:
    chain_run_id: int
    completed: list[str] = field(default_factory=list)
    failed: str | None = None
    notes: list[str] = field(default_factory=list)


def run_chain(ledger: RunLedger, clock: Clock, config_hash: str, steps: list[Step], logger: logging.Logger | None = None) -> ChainResult:
    log = logger or logging.getLogger("jobpipe.chain")
    previous = ledger.interrupted(RunPhase.CHAIN)
    if previous is not None:
        ledger.finish(previous.id, RunStatus.INTERRUPTED, "superseded by a newer chain run")
    chain = ledger.start(RunPhase.CHAIN, config_hash)
    result = ChainResult(chain_run_id=chain.id)
    try:
        for step in steps:
            ledger.checkpoint(chain.id, step.name)
            try:
                note = step.action(chain.id)
            except Exception as exc:
                result.failed = step.name
                result.notes.append(f"{step.name}: {type(exc).__name__}: {exc}")
                log.warning("chain step %s failed: %s", step.name, exc)
                ledger.finish(chain.id, RunStatus.FAILED, result.notes[-1])
                return result
            result.completed.append(step.name)
            result.notes.append(f"{step.name}: {note}")
    except KeyboardInterrupt:
        ledger.finish(chain.id, RunStatus.INTERRUPTED, f"interrupted during {ledger.get(chain.id).last_job_id}")
        raise
    ledger.finish(chain.id, RunStatus.OK, "; ".join(result.notes)[:500])
    return result
