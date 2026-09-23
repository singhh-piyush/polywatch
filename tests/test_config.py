from pathlib import Path

import pytest

from polywatch.config import Settings, load_settings


def test_defaults_when_file_missing(tmp_path):
    assert load_settings(tmp_path / "missing.toml") == Settings()


def test_overrides_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('window_days = 60\nwatchlist_size = 25\ncandidate_depths = [["MONTH", 200]]\ndb_path = "~/pw.db"\n')
    cfg = load_settings(path)
    assert cfg.window_days == 60 and cfg.watchlist_size == 25
    assert cfg.candidate_depths == (("MONTH", 200),)
    assert cfg.db_path == Path("~/pw.db").expanduser()
    assert cfg.active_days == Settings().active_days


def test_unknown_key_is_an_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("windw_days = 60\n")
    with pytest.raises(ValueError, match="windw_days"):
        load_settings(path)


def test_paths_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Settings()
    assert cfg.db_path == tmp_path / "data" / "polywatch" / "polywatch.db"
    assert cfg.log_path == tmp_path / "state" / "polywatch" / "polywatch.log"
