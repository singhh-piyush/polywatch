from rich.console import Console

from polywatch import cli
from polywatch.config import Settings
from polywatch.discovery.pipeline import ScanProgress
from polywatch.models import RankedTrader, Verdict
from tests.factories import stats


def render(renderable) -> str:
    console = Console(record=True, width=250)
    console.print(renderable)
    return console.export_text()


def test_ranked_table_lists_traders_with_profile_links():
    ranked = [RankedTrader(stats(wallet="0xaaa", username="alice"), Verdict(), score=91, rank=1)]
    text = render(cli.ranked_table(ranked, top=50))
    assert "alice" in text and "91" in text and "polymarket.com/profile/0xaaa" in text


def test_ranked_table_does_not_parse_markup_in_names():
    ranked = [RankedTrader(stats(username="[bold]x[/bold]"), Verdict(), score=1, rank=1)]
    assert "[bold]x[/bold]" in render(cli.ranked_table(ranked, top=5))


def test_excluded_summary_groups_by_category():
    traders = [
        RankedTrader(stats(wallet="0x1"), Verdict(excluded="inactive: last trade 20d ago")),
        RankedTrader(stats(wallet="0x2"), Verdict(excluded="inactive: no recent trades")),
        RankedTrader(stats(wallet="0x3"), Verdict(excluded="bot: 99% of trades in 5/15-min markets")),
        RankedTrader(stats(wallet="0x4"), Verdict()),
    ]
    assert cli.excluded_summary(traders) == {"inactive": 2, "bot": 1}
    assert "no recent trades" in render(cli.excluded_table(traders))


def test_main_dispatches_discover(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "setup_logging", lambda path: None)
    calls = []

    async def fake_discover(cfg, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "discover", fake_discover)
    cli.main(["discover", "--limit", "5", "--show-excluded"])
    assert calls == [{"limit": 5, "show_excluded": True, "top": 50}]


def test_main_without_command_launches_the_tui(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "setup_logging", lambda path: None)
    launched = []

    class FakeApp:
        def __init__(self, cfg):
            launched.append(cfg)

        def run(self):
            launched.append("ran")

    import polywatch.tui.app
    monkeypatch.setattr(polywatch.tui.app, "PolywatchApp", FakeApp)
    cli.main([])
    assert launched[-1] == "ran"


async def test_discover_reports_the_scan_it_just_ran(tmp_path, monkeypatch):
    class FakeScanner:
        def __init__(self, *args):
            pass

        async def run(self, *, limit, on_progress):
            excluded = RankedTrader(stats(wallet="0xbot", username="bot"),
                                    Verdict(excluded="bot: 30,000 markets traded"))
            ranked = RankedTrader(stats(), Verdict(), score=90, rank=1)
            for t in (excluded, ranked):
                on_progress(ScanProgress(done=1, total=2, stats=t.stats, verdict=t.verdict))
            return [ranked]

    monkeypatch.setattr(cli, "Scanner", FakeScanner)
    console = Console(record=True, width=200)
    await cli.discover(Settings(db_path=tmp_path / "db.sqlite"), limit=2, show_excluded=True, top=5, console=console)
    out = console.export_text()
    assert "1 ranked, 1 excluded (bot 1)" in out and "30,000 markets traded" in out
