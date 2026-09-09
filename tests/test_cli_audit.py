import asyncio
import json

import pytest

import lights


@pytest.fixture
def isolated_lights_state(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "ALERT_PULSE_TOGGLE", tmp_path / "alert_pulse.toggle")
    monkeypatch.setattr(lights, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(lights, "STATE_FILE", tmp_path / "last_mode")
    monkeypatch.setattr(lights, "EFFECT_FILE", tmp_path / "effect_running")
    monkeypatch.setattr(lights, "EFFECT_BRI_FILE", tmp_path / "effect_bri")
    monkeypatch.setattr(lights, "PENDING_MODE_FILE", tmp_path / "pending_mode")
    monkeypatch.setattr(lights, "AUDIT_LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(lights, "EFFECT_LOG_FILE", tmp_path / "effects.log")
    monkeypatch.setattr(lights, "COOK_OVERRIDE_FILE", tmp_path / "cook_override.json")
    monkeypatch.setattr(lights, "COOK_DEBOUNCE_FILE", tmp_path / "cook_last_press")
    monkeypatch.setattr(lights, "COOK_LOCK_FILE", tmp_path / "cook.lock")
    monkeypatch.setattr(lights, "COOK_LOG_FILE", tmp_path / "cook.log")
    monkeypatch.setattr(lights, "DRY_RUN", False)
    monkeypatch.setattr(lights, "target_power_state", lambda: True)
    lights._set_active_group(None)
    return tmp_path


def read_audit_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_current_mode_polling_does_not_audit_success(isolated_lights_state, capsys):
    lights.STATE_FILE.write_text("warm")

    asyncio.run(lights.main(["current-mode"]))

    assert capsys.readouterr().out == "warm\n"
    assert read_audit_records(lights.AUDIT_LOG_FILE) == []


def test_current_mode_underscore_alias_output_is_unchanged(isolated_lights_state, capsys):
    lights.STATE_FILE.write_text("warm")

    asyncio.run(lights.main(["current_mode"]))

    assert capsys.readouterr().out == "warm\n"
    assert read_audit_records(lights.AUDIT_LOG_FILE) == []


def test_state_changing_commands_still_audit(isolated_lights_state, monkeypatch, capsys):
    monkeypatch.setattr(lights, "DRY_RUN", True)

    asyncio.run(lights.main(["off"]))

    assert capsys.readouterr().out == "DRY_RUN      cmd=off group=all args=[]\n"
    records = read_audit_records(lights.AUDIT_LOG_FILE)
    assert len(records) == 1
    assert records[0]["event"] == "invoke"
    assert records[0]["phase"] == "cli"
    assert records[0]["group"] == "all"
    assert records[0]["cmd"] == "off"
    assert records[0]["args"] == []


def test_current_mode_errors_are_still_audited(isolated_lights_state, monkeypatch):
    def raise_current_mode_error():
        raise RuntimeError("state read failed")

    monkeypatch.setattr(lights, "current_mode_names", raise_current_mode_error)

    with pytest.raises(RuntimeError, match="state read failed"):
        asyncio.run(lights.main(["current-mode"]))

    records = read_audit_records(lights.AUDIT_LOG_FILE)
    assert len(records) == 1
    assert records[0]["event"] == "cli_error"
    assert records[0]["phase"] == "cli"
    assert records[0]["group"] == "all"
    assert records[0]["cmd"] == "current_mode"
    assert records[0]["args"] == []
    assert records[0]["error"] == "RuntimeError: state read failed"
