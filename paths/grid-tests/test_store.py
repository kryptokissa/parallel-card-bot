"""State that survives between invocations, and where it is allowed to live."""

from __future__ import annotations

from pathlib import Path

from engine.store import GridState, default_store_path, load, save


def test_the_store_never_defaults_inside_the_path_directory(monkeypatch, path_dir):
    # Everything under the path directory ships in the published bundle, and a
    # save file full of real positions is the last thing that should.
    monkeypatch.delenv("GRID_STATE_PATH", raising=False)
    monkeypatch.delenv("WAYFINDER_CONFIG_PATH", raising=False)
    target = default_store_path("BTC-USDC")
    assert path_dir not in target.parents
    assert target != path_dir


def test_an_explicit_override_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("GRID_STATE_PATH", str(tmp_path / "somewhere.json"))
    assert default_store_path("BTC-USDC") == tmp_path / "somewhere.json"


def test_market_names_are_made_filesystem_safe(monkeypatch):
    monkeypatch.delenv("GRID_STATE_PATH", raising=False)
    monkeypatch.delenv("WAYFINDER_CONFIG_PATH", raising=False)
    assert "/" not in default_store_path("xyz:SP500").name
    assert ":" not in default_store_path("xyz:SP500").name


def test_a_missing_store_loads_as_empty_and_names_itself(tmp_path):
    target = tmp_path / "absent.json"
    state = load(target)
    assert state.config == {}
    assert not state.started
    # A state command that cannot name its save file is unfalsifiable when two
    # runs disagree.
    assert state.store_path == str(target)


def test_round_trip_preserves_config_provenance_and_events(tmp_path, base_config):
    from engine.config import GridConfig

    target = tmp_path / "state.json"
    state = GridState()
    state.config = {k: getattr(base_config, k) for k in GridConfig.__dataclass_fields__}
    state.provenance = {"breakout": "delegated"}
    state.started = True
    state.log("grid_started", market="BTC-USDC")
    save(state, target)

    reloaded = load(target)
    assert reloaded.started
    assert reloaded.provenance == {"breakout": "delegated"}
    assert reloaded.grid_config() == base_config
    assert reloaded.events[0]["event"] == "grid_started"
    assert "at" in reloaded.events[0]


def test_unknown_fields_in_a_stored_file_are_ignored(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"started": true, "from_a_future_version": 1}\n')
    assert load(target).started


def test_the_event_log_is_bounded(tmp_path):
    target = tmp_path / "state.json"
    state = GridState()
    for index in range(600):
        state.log("tick", index=index)
    save(state, target)
    assert len(load(target).events) == 500
