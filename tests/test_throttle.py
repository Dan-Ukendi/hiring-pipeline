from jobpipe.http.throttle import Throttle
from tests.conftest import FakeClock


def test_requests_to_one_domain_are_spaced_by_the_rate() -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 1.0, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(4):
        throttle.wait("a.test")
    assert clock.sleeps == [1.0, 1.0, 1.0]
    assert clock.now == 3.0


def test_rate_is_per_domain() -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 1.0, clock=clock.monotonic, sleep=clock.sleep)
    throttle.wait("a.test")
    throttle.wait("b.test")
    assert clock.sleeps == []
    throttle.wait("a.test")
    assert clock.sleeps == [1.0]


def test_configured_rate_changes_the_spacing() -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 4.0 if domain == "fast.test" else 0.5, clock=clock.monotonic, sleep=clock.sleep)
    throttle.wait("fast.test")
    throttle.wait("fast.test")
    throttle.wait("slow.test")
    throttle.wait("slow.test")
    assert clock.sleeps == [0.25, 2.0]


def test_no_sleep_when_enough_time_has_passed() -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 1.0, clock=clock.monotonic, sleep=clock.sleep)
    throttle.wait("a.test")
    clock.now += 5.0
    assert throttle.wait("a.test") == 0.0
    assert clock.sleeps == []
