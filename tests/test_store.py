from polywatch.config import Settings
from polywatch.discovery.scoring import rank_traders
from polywatch.models import FeedItem, Verdict
from polywatch.store import Store
from tests.factories import NOW, stats


def seed(store: Store, scan_ts: int = NOW) -> int:
    scan = store.start_scan(scan_ts)
    entries = [
        (stats(wallet="0xa", username="alice"), Verdict()),
        (stats(wallet="0xb", username="bob", edge=0.0), Verdict(flags=("NEW",))),
        (stats(wallet="0xc", username="carol"), Verdict(excluded="inactive: last trade 30d ago")),
    ]
    for s, v in entries:
        store.save_trader(scan, s, v)
    store.finish_scan(scan, rank_traders(entries, Settings()), candidates=3, now=scan_ts + 60)
    return scan


def test_scan_roundtrip():
    store = Store(":memory:")
    scan = seed(store)
    assert store.latest_scan() == (scan, NOW + 60)
    traders = store.load_scan(scan)
    assert [t.stats.wallet for t in traders] == ["0xa", "0xb", "0xc"]
    assert traders[0].rank == 1 and traders[0].score > traders[1].score
    assert traders[1].verdict.flags == ("NEW",)
    assert traders[2].verdict.excluded == "inactive: last trade 30d ago" and traders[2].rank == 0
    assert traders[0].stats == stats(wallet="0xa", username="alice")


def test_unfinished_scans_are_ignored():
    store = Store(":memory:")
    seed(store)
    store.start_scan(NOW + 1000)
    assert store.latest_scan()[1] == NOW + 60


def test_only_recent_scans_are_kept():
    store = Store(":memory:")
    for i in range(7):
        seed(store, NOW + i)
    assert len(store.db.execute("SELECT DISTINCT scan_id FROM trader_stats").fetchall()) == 5
    assert store.db.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 5


def test_overrides():
    store = Store(":memory:")
    store.set_override("0xa", "pin", name="alice", now=1)
    store.set_override("0xb", "ban", now=1)
    assert store.overrides() == {"0xa": "pin", "0xb": "ban"}
    store.set_override("0xa", "ban", now=2)
    assert store.overrides()["0xa"] == "ban"
    assert store.override_names() == {"0xa": "alice"}
    store.set_override("0xa", None)
    assert "0xa" not in store.overrides()


def test_feed_items_upsert_and_parent_dir_creation(tmp_path):
    store = Store(tmp_path / "nested" / "pw.db")
    item = FeedItem(key="k1", wallet="0xa", name="alice", side="BUY", asset="a1", title="t", outcome="Yes",
                    slug="s", event_slug="e", first_ts=1, last_ts=2, shares=10, usd=5, fills=1)
    store.save_feed_item(item)
    item.usd, item.fills = 50, 2
    store.save_feed_item(item)
    assert store.db.execute("SELECT usd, fills FROM feed_events").fetchall() == [(50.0, 2)]
    store.close()


def test_prefs_round_trip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    assert store.get_pref("my_wallet") is None
    store.set_pref("my_wallet", "0xme")
    store.set_pref("my_wallet", "0xme2")
    store.close()
    reopened = Store(tmp_path / "db.sqlite")
    assert reopened.get_pref("my_wallet") == "0xme2"
    reopened.set_pref("my_wallet", None)
    assert reopened.get_pref("my_wallet") is None
