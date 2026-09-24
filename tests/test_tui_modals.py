from textual.app import App
from textual.widgets import Static

from polywatch.api.http import ApiError
from polywatch.models import RankedTrader, Verdict
from polywatch.tui.add import AddTrader, parse_trader_ref
from polywatch.tui.detail import TraderDetail, detail_text, positions_text
from tests.factories import NOW, PINNED, position_row, stats

WALLET = "0xabc" + "0" * 36 + "1"


class ScreenHost(App):
    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.results = []

    def on_mount(self):
        self.push_screen(self._screen, self.results.append)


class FakeData:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc = rows or [], exc

    async def positions(self, wallet, max_pages=20):
        if self.exc:
            raise self.exc
        return self.rows


class FakeGamma:
    def __init__(self, matches):
        self.matches = matches

    async def search_profiles(self, query, limit=5):
        return self.matches


def test_detail_text_lists_metrics_and_flags():
    t = RankedTrader(stats(username="alice", top_share=0.42, fast_share=0.12), Verdict(flags=("CONC",)),
                     score=81, rank=4)
    text = detail_text(t, PINNED, now=NOW).plain
    assert "alice" in text and "Rank #4 · score 81/100" in text
    assert "Win rate" in text and "65%" in text
    assert "Too fast to copy" in text and "12%" in text
    assert "CONC: one bet is 42% of 90-day profit" in text
    assert "1d ago" in text


def test_detail_text_for_excluded_trader():
    t = RankedTrader(stats(username="bob"), Verdict(excluded="inactive: last trade 30d ago"))
    assert "Not ranked: inactive: last trade 30d ago" in detail_text(t, PINNED, now=NOW).plain


def test_positions_text_lists_open_positions_by_value():
    rows = [
        {**position_row("a1", avg_price=0.4, size=100, cur_price=0.5, redeemable=False), "title": "Small"},
        {**position_row("a2", avg_price=0.2, size=1000, cur_price=0.3, redeemable=False), "title": "Big", "outcome": "No"},
        {**position_row("a3"), "title": "Resolved"},
    ]
    text = positions_text(rows).plain
    assert text.index("Big") < text.index("Small") and "Resolved" not in text
    assert "20¢ → 30¢   $300 (+$100)" in text
    assert positions_text([]).plain.endswith("none")


async def test_detail_screen_reports_position_errors_and_opens_profile():
    opened = []
    trader = RankedTrader(stats(username="alice"), Verdict(), score=50, rank=1)
    screen = TraderDetail(trader, PINNED, FakeData(exc=ApiError("offline")), opened.append)
    async with ScreenHost(screen).run_test() as pilot:
        await pilot.pause()
        assert "Couldn't load open positions" in str(screen.query_one("#positions", Static).content)
        await pilot.press("o")
        assert opened == ["https://polymarket.com/profile/0xsharp"]


def test_parse_trader_ref():
    assert parse_trader_ref(f"  {WALLET.upper().replace('0X', '0x')} ") == ("wallet", WALLET)
    assert parse_trader_ref(f"https://polymarket.com/profile/{WALLET}") == ("wallet", WALLET)
    assert parse_trader_ref("https://polymarket.com/@Fredi9999/") == ("name", "Fredi9999")
    assert parse_trader_ref("@swisstony") == ("name", "swisstony")
    assert parse_trader_ref("   ") == ("name", "")


async def test_add_by_wallet_dismisses_immediately():
    host = ScreenHost(AddTrader(FakeGamma([])))
    async with host.run_test() as pilot:
        await pilot.press(*WALLET, "enter")
        await pilot.pause()
    assert host.results == [(WALLET, "")]


async def test_add_by_name_searches_then_picks_a_match():
    host = ScreenHost(AddTrader(FakeGamma([("Fredi9999", "0xf1"), ("fredi-2", "0xf2")])))
    async with host.run_test() as pilot:
        await pilot.press(*"fredi", "enter")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
    assert host.results == [("0xf2", "fredi-2")]


async def test_add_by_name_without_matches_explains():
    screen = AddTrader(FakeGamma([]))
    host = ScreenHost(screen)
    async with host.run_test() as pilot:
        await pilot.press(*"nobody", "enter")
        await pilot.pause()
        assert "No trader named 'nobody'" in str(screen.query_one("#message", Static).content)
        await pilot.press("escape")
        await pilot.pause()
    assert host.results == [None]


async def test_add_trader_prompt_can_be_changed():
    screen = AddTrader(FakeGamma([]), prompt="Your Polymarket account")
    async with ScreenHost(screen).run_test() as pilot:
        await pilot.pause()
        assert "Your Polymarket account" in str(screen.query_one("Label").content)
