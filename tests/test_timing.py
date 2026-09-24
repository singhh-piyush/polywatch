from polywatch.feed.timing import MarketTimes
from polywatch.models import MarketTiming

OPEN = MarketTiming(start_ts=100, end_ts=200)
CLOSED = MarketTiming(start_ts=100, end_ts=200, closed=True)


class FakeGamma:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def market_timing(self, slug):
        self.calls.append(slug)
        return self.answers.get(slug)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_new_slugs_are_due_once_then_cached():
    clock = Clock()
    times = MarketTimes(FakeGamma({"a": OPEN}), refresh_s=300, clock=clock)
    assert times.take_due(["a", "a"]) == ["a"]
    assert times.take_due(["a"]) == []  # already claimed, so a burst of fills fetches once
    assert times.cached("a") is None
    assert await times.fetch("a") == OPEN and times.cached("a") == OPEN


async def test_open_markets_are_rechecked_and_closed_ones_are_not():
    clock = Clock()
    gamma = FakeGamma({"open": OPEN, "done": CLOSED})
    times = MarketTimes(gamma, refresh_s=300, clock=clock)
    for slug in times.take_due(["open", "done"]):
        await times.fetch(slug)
    clock.now += 299
    assert times.take_due(["open", "done"]) == []
    clock.now += 1
    assert times.take_due(["open", "done"]) == ["open"]


async def test_a_failed_refresh_keeps_the_last_answer():
    clock = Clock()
    gamma = FakeGamma({"a": OPEN})
    times = MarketTimes(gamma, refresh_s=300, clock=clock)
    await times.fetch("a")
    gamma.answers = {}
    clock.now += 300
    assert await times.fetch("a") == OPEN and times.cached("a") == OPEN
