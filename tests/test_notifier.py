import subprocess

from polywatch.feed.notifier import Notifier


class Runner:
    def __init__(self, stdout="", exc=None):
        self.calls, self.stdout, self.exc = [], stdout, exc

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, 0, stdout=self.stdout, stderr="")


def make(runner, opened, **kw):
    return Notifier(runner=runner, opener=opened.append, spawn=lambda fn: fn(), available=True, **kw)


def test_sends_notification_with_open_action_and_escaped_body():
    runner, opened = Runner(), []
    make(runner, opened).notify("alice: BUY", "Chelsea & Co <b>", "https://polymarket.com/event/x")
    [args] = runner.calls
    assert args == ["notify-send", "--app-name=polywatch", "--action=open=Open market", "alice: BUY",
                    "Chelsea &amp; Co &lt;b&gt;"]
    assert opened == []


def test_clicking_open_opens_the_market():
    opened = []
    make(Runner(stdout="open\n"), opened).notify("t", "b", "https://polymarket.com/event/x")
    assert opened == ["https://polymarket.com/event/x"]


def test_disabled_or_unavailable_does_nothing():
    runner = Runner()
    Notifier(enabled=False, runner=runner, spawn=lambda fn: fn(), available=True).notify("t", "b", "u")
    Notifier(runner=runner, spawn=lambda fn: fn(), available=False).notify("t", "b", "u")
    assert runner.calls == []


def test_failures_are_swallowed():
    make(Runner(exc=FileNotFoundError("notify-send")), []).notify("t", "b", "u")
    make(Runner(exc=subprocess.TimeoutExpired("notify-send", 1)), []).notify("t", "b", "u")
