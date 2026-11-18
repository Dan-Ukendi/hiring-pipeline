from collections import Counter
from dataclasses import dataclass, field

from jobpipe.contract.enums import Feasibility
from jobpipe.contract.models import ApplyPlanBundle


@dataclass
class ApplyEvaluation:
    cases: int = 0
    route_matrix: Counter = field(default_factory=Counter)
    route_accuracy: float | None = None
    auto_full_predicted: int = 0
    false_auto_full: int = 0
    feasibility_matrix: Counter = field(default_factory=Counter)
    missing: list[str] = field(default_factory=list)

    @property
    def false_auto_full_rate(self) -> float | None:
        return self.false_auto_full / self.auto_full_predicted if self.auto_full_predicted else None


def evaluate(cases: list[dict], bundles: dict[str, ApplyPlanBundle]) -> ApplyEvaluation:
    result = ApplyEvaluation()
    correct = 0
    for case in cases:
        bundle = bundles.get(case["job_id"])
        if bundle is None:
            result.missing.append(case["job_id"])
            continue
        result.cases += 1
        plan = bundle.plan
        expected_route, expected_level = case["route"], case["feasibility"]
        result.route_matrix[(expected_route, plan.route.value)] += 1
        result.feasibility_matrix[(expected_level, plan.feasibility.value)] += 1
        correct += int(expected_route == plan.route.value)
        if plan.feasibility is Feasibility.AUTO_FULL:
            result.auto_full_predicted += 1
            result.false_auto_full += int(expected_level != Feasibility.AUTO_FULL.value)
    result.route_accuracy = correct / result.cases if result.cases else None
    return result
