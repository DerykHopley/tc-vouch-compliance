"""RateLimit on its own: counted per user, in a sliding window, with a clock the test moves."""

import pytest

from vouch.limits import RateLimit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_rate_is_counted_per_user():
    limit = RateLimit(requests=1, per_seconds=60, clock=Clock())

    assert limit.wait_for("lead") is None
    assert limit.wait_for("developer") is None
    assert limit.wait_for("lead") == 60


@pytest.mark.parametrize("requests", [0, -1])
def test_a_limit_below_one_request_is_refused_when_made(requests):
    with pytest.raises(ValueError, match="at least 1"):
        RateLimit(requests=requests, per_seconds=60)


@pytest.mark.parametrize("per_seconds", [0, -5])
def test_a_window_that_isnt_positive_is_refused_when_made(per_seconds):
    with pytest.raises(ValueError, match="positive"):
        RateLimit(requests=1, per_seconds=per_seconds)


def test_users_idle_for_a_whole_window_are_forgotten():
    clock = Clock()
    limit = RateLimit(requests=5, per_seconds=60, clock=clock)
    for user in ("a", "b", "c"):
        limit.wait_for(user)

    clock.now += 61
    limit.wait_for("d")

    assert limit.users() == {"d"}


def test_a_user_seen_within_the_window_is_kept_by_the_sweep():
    clock = Clock()
    limit = RateLimit(requests=5, per_seconds=60, clock=clock)
    limit.wait_for("a")
    clock.now += 30
    limit.wait_for("b")

    clock.now += 31  # a idle 61 s, b 31 s
    limit.wait_for("c")

    assert limit.users() == {"b", "c"}
