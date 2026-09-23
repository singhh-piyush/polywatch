from polywatch.api.http import ApiError
from polywatch.discovery.pipeline import Scanner
from polywatch.models import LeaderboardEntry
from polywatch.store import Store
from tests.factories import DAY, NOW, PINNED, closed_row, position_row, trade_row


class FakeData:
    def __init__(self, wallets, leaderboards):
        self.wallets = wallets
        self.leaderboards = leaderboards
        self.calls = []

    def _w(self, wallet):
        spec = self.wallets[wallet]
        if isinstance(spec, Exception):
            raise spec
        return spec

    async def leaderboard(self, period, depth):
        return self.leaderboards.get(period, [])[:depth]

    async def traded(self, wallet):
        self.calls.append(("traded", wallet))
        return self._w(wallet)["traded"]

    async def activity(self, wallet, *, type_="TRADE", start=None, limit=500):
        self.calls.append((type_, wallet))
        spec = self._w(wallet)
        return spec["trades"] if type_ == "TRADE" else spec.get("rebates", [])

    async def closed_positions(self, wallet, since_ts, max_pages):
        self.calls.append(("closed", wallet))
        return [r for r in self._w(wallet)["closed"] if r["timestamp"] >= since_ts], False

    async def positions(self, wallet, max_pages=20):
        self.calls.append(("positions", wallet))
        return self._w(wallet).get("positions", [])


class FakeGamma:
    async def account_created_ts(self, wallet):
        return NOW - 400 * DAY


def active_trades(n=300, slug="some-market", hours=range(8, 24), price=0.5):
    hours = list(hours)
    yesterday = (NOW // DAY) * DAY - DAY
    return [trade_row(yesterday - (i % 5) * DAY + hours[i % len(hours)] * 3600 + (i % 60) * 60, slug=slug,
                      price=price) for i in range(n)]


def sharp_closed(n_bets=40, wins=26, price=0.45, stake=1000.0):
    return [closed_row(f"c{i}", stake * (1 - price) / price if i < wins else -stake, avg_price=price,
                       bought=stake / price, ts=NOW - (i + 1) * DAY) for i in range(n_bets)]


def wallet(**kw):
    spec = {"traded": 300, "trades": active_trades(), "closed": sharp_closed(), "positions": [], "rebates": []}
    spec.update(kw)
    return spec


def build():
    hidden_losers = [position_row(f"h{i}", avg_price=0.45, size=2222.0, cur_price=0.0) for i in range(10)]
    one_hit = [closed_row("big", 100_000.0, ts=NOW - DAY)] + [closed_row(f"s{i}", 100.0, ts=NOW - 2 * DAY)
                                                              for i in range(19)]
    voided = [closed_row(f"v{i}", 2.0, avg_price=0.49, cur_price=0.5, ts=NOW - DAY) for i in range(60)]
    wallets = {
        "0xsharp": wallet(),
        "0xhidden": wallet(positions=hidden_losers),
        "0xmm": wallet(),
        "0xonehit": wallet(closed=one_hit),
        "0xsleepy": wallet(trades=[trade_row(NOW - 30 * DAY)]),
        "0xfivemin": wallet(trades=active_trades(slug="btc-updown-5m-1790000000")),
        "0xsniper": wallet(trades=active_trades(price=0.97)),
        "0xvoider": wallet(closed=sharp_closed() + voided),
        "0xbroken": ApiError("boom"),
    }
    board = [LeaderboardEntry(w, w[2:], 50_000.0, 400_000.0) for w in wallets]
    board[2] = LeaderboardEntry("0xmm", "mm", 100_000.0, 20_000_000.0)
    return FakeData(wallets, {"MONTH": board})


async def test_scan_ranks_sharps_and_explains_exclusions():
    data, store, progress = build(), Store(":memory:"), []
    scanner = Scanner(data, FakeGamma(), store, PINNED, clock=lambda: NOW)
    ranked = await scanner.run(on_progress=progress.append)

    assert [t.stats.wallet for t in ranked] == ["0xsharp", "0xhidden"]
    hidden = ranked[1].stats
    assert hidden.n == 50 and hidden.win_rate == 26 / 50  # the 10 unredeemed losers count

    verdicts = {p.stats.wallet: p.verdict for p in progress}
    assert verdicts["0xmm"].excluded.startswith("market maker")
    assert verdicts["0xonehit"].excluded.startswith("one-hit")
    assert verdicts["0xsleepy"].excluded.startswith("inactive")
    assert verdicts["0xfivemin"].excluded.startswith("bot")
    assert verdicts["0xsniper"].excluded.startswith("too fast to copy")
    assert verdicts["0xvoider"].excluded.startswith("void arb")
    assert verdicts["0xbroken"].excluded == "error: boom"
    assert [p.done for p in progress] == list(range(1, 10)) and progress[-1].total == 9

    assert ("closed", "0xmm") not in data.calls  # cheap checks stop before the expensive fetches
    assert ("closed", "0xsleepy") not in data.calls
    assert ("closed", "0xsniper") not in data.calls

    scan_id, _ = store.latest_scan()
    assert len(store.load_scan(scan_id)) == 9


async def test_candidates_merge_leaderboards_and_add_pins():
    e = lambda w: LeaderboardEntry(w, w, 1.0, 1.0)
    data = FakeData({}, {"MONTH": [e("0xa"), e("0xb")], "WEEK": [e("0xb"), e("0xc")]})
    scanner = Scanner(data, FakeGamma(), Store(":memory:"), PINNED, clock=lambda: NOW)
    pool = await scanner.candidates({"0xpin"})
    assert list(pool) == ["0xa", "0xb", "0xc", "0xpin"] and pool["0xpin"] is None
    limited = await scanner.candidates({"0xpin"}, limit=2)
    assert list(limited) == ["0xa", "0xb", "0xpin"]
