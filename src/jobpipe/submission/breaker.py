from dataclasses import dataclass, field

from jobpipe.submission.models import ErrorClass


@dataclass
class VendorStats:
    attempts: int = 0
    consecutive_failures: int = 0
    disabled: bool = False
    reason: str | None = None


@dataclass
class CircuitBreaker:
    vendor_failure_limit: int = 3
    run_failure_limit: int = 5
    unconfirmed_limit: int = 2
    failure_rate_limit: float = 0.5
    failure_rate_min_attempts: int = 4
    vendors: dict[str, VendorStats] = field(default_factory=dict)
    consecutive_failures: int = 0
    unconfirmed: int = 0
    attempts: int = 0
    failures: int = 0
    stopped: str | None = None
    permanently_blocked: set[str] = field(default_factory=set)

    def _vendor(self, name: str) -> VendorStats:
        return self.vendors.setdefault(name, VendorStats())

    def allows(self, vendor: str) -> bool:
        return self.stopped is None and not self._vendor(vendor).disabled

    def record_success(self, vendor: str) -> None:
        stats = self._vendor(vendor)
        stats.attempts += 1
        stats.consecutive_failures = 0
        self.attempts += 1
        self.consecutive_failures = 0

    def record_unconfirmed(self, vendor: str) -> None:
        self._vendor(vendor).attempts += 1
        self.attempts += 1
        self.unconfirmed += 1
        if self.unconfirmed >= self.unconfirmed_limit:
            self.stopped = "two unconfirmed submissions in one run"

    def record_failure(self, vendor: str, error: ErrorClass) -> None:
        stats = self._vendor(vendor)
        stats.attempts += 1
        stats.consecutive_failures += 1
        self.attempts += 1
        self.failures += 1
        self.consecutive_failures += 1
        if error is ErrorClass.AUTH_REQUIRED:
            stats.disabled, stats.reason = True, "authentication was requested; endpoint is protected"
            self.permanently_blocked.add(vendor)
        elif error is ErrorClass.CAPTCHA_BLOCKED:
            stats.disabled, stats.reason = True, "a challenge was served"
        elif stats.consecutive_failures >= self.vendor_failure_limit:
            stats.disabled, stats.reason = True, f"{stats.consecutive_failures} consecutive failures"
        if self.consecutive_failures >= self.run_failure_limit:
            self.stopped = f"{self.consecutive_failures} consecutive failures"
        elif self.attempts >= self.failure_rate_min_attempts and self.failures / self.attempts > self.failure_rate_limit:
            self.stopped = "failure rate above 50 percent"
