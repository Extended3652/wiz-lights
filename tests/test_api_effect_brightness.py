import asyncio

import lights
import lights_api


class FakeState:
    def __init__(self, *, rgb=None, colortemp=None):
        self.rgb = rgb
        self.colortemp = colortemp

    def get_rgb(self):
        return self.rgb

    def get_colortemp(self):
        return self.colortemp


class FakeBulb:
    def __init__(self, ip, state):
        self.ip = ip
        self.state = state
        self.turn_on_calls = []
        self.closed = False

    async def updateState(self):
        return self.state

    async def turn_on(self, pilot):
        self.turn_on_calls.append(pilot)

    async def async_close(self):
        self.closed = True


class FakePilot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def write_effect(tmp_path, group, name="test-effect", pid=123):
    (tmp_path / f"effect_running_{group}").write_text(f"{name}\n{pid}\n")


def install_bulbs(monkeypatch, states=None):
    states = states or {}
    bulbs = {}

    def make_bulb(ip):
        bulb = FakeBulb(ip, states.get(ip, FakeState()))
        bulbs[ip] = bulb
        return bulb

    monkeypatch.setattr(lights_api, "wizlight", make_bulb)
    monkeypatch.setattr(lights_api, "PilotBuilder", FakePilot)
    return bulbs


def test_all_room_effect_brightness_updates_effect_scale_without_bulbs(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    effect_file = tmp_path / "effect_running_all"
    effect_file.write_text("bonfire\n123\n")
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("bulb should not be created"))

    result = asyncio.run(lights_api.set_group_brightness("all", 155))

    assert (tmp_path / "effect_bri_all").read_text() == "155"
    assert effect_file.read_text() == "bonfire\n123\n"
    assert lights.effect_is_running("all")
    assert result == {
        "room": "all",
        "brightness": 155,
        "bulbs": [
            {"ip": "192.168.86.123", "ok": True, "brightness": 155},
            {"ip": "192.168.86.124", "ok": True, "brightness": 155},
            {"ip": "192.168.86.133", "ok": True, "brightness": 155},
            {"ip": "192.168.86.134", "ok": True, "brightness": 155},
        ],
        "effect_group": "all",
    }


def test_room_slider_uses_all_effect_scale_when_all_effect_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    (tmp_path / "effect_running_all").write_text("bonfire\n123\n")
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("bulb should not be created"))

    result = asyncio.run(lights_api.set_group_brightness("kitchen", 77))

    assert (tmp_path / "effect_bri_all").read_text() == "77"
    assert result["room"] == "kitchen"
    assert result["brightness"] == 77
    assert result["bulbs"] == [
        {"ip": "192.168.86.123", "ok": True, "brightness": 77},
        {"ip": "192.168.86.124", "ok": True, "brightness": 77},
    ]
    assert result["effect_group"] == "all"


def test_room_effect_brightness_uses_room_effect_scale(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    (tmp_path / "effect_running_entryway").write_text("aurora\n456\n")
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("bulb should not be created"))

    result = asyncio.run(lights_api.set_group_brightness("entryway", 300))

    assert (tmp_path / "effect_bri_entryway").read_text() == "255"
    assert result["brightness"] == 255
    assert result["effect_group"] == "entryway"


def test_all_room_with_kitchen_effect_partitions_direct_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    write_effect(tmp_path, "kitchen")
    bulbs = install_bulbs(monkeypatch, {
        "192.168.86.133": FakeState(colortemp=4200),
        "192.168.86.134": FakeState(colortemp=4200),
    })

    result = asyncio.run(lights_api.set_group_brightness("all", 100))

    assert (tmp_path / "effect_bri_kitchen").read_text() == "100"
    assert lights.effect_is_running("kitchen")
    assert all(ip not in bulbs for ip in ("192.168.86.123", "192.168.86.124"))
    assert all(
        len(bulbs[ip].turn_on_calls) == 1
        for ip in ("192.168.86.133", "192.168.86.134")
    )
    assert all(
        bulbs[ip].turn_on_calls[0].kwargs["colortemp"] == 4200
        for ip in ("192.168.86.133", "192.168.86.134")
    )
    assert result["effect_group"] == "kitchen"


def test_all_room_with_entryway_effect_partitions_direct_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    write_effect(tmp_path, "entryway")
    bulbs = install_bulbs(monkeypatch, {
        "192.168.86.123": FakeState(colortemp=3500),
        "192.168.86.124": FakeState(colortemp=3500),
    })

    result = asyncio.run(lights_api.set_group_brightness("all", 100))

    assert (tmp_path / "effect_bri_entryway").read_text() == "100"
    assert lights.effect_is_running("entryway")
    assert all(ip not in bulbs for ip in ("192.168.86.133", "192.168.86.134"))
    assert all(
        len(bulbs[ip].turn_on_calls) == 1
        for ip in ("192.168.86.123", "192.168.86.124")
    )
    assert result["effect_group"] == "entryway"


def test_all_room_with_both_room_effects_never_commands_bulbs(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    write_effect(tmp_path, "kitchen", pid=123)
    write_effect(tmp_path, "entryway", pid=456)
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("effect-controlled bulb should not be created"))

    result = asyncio.run(lights_api.set_group_brightness("all", 0))

    assert (tmp_path / "effect_bri_kitchen").read_text() == "1"
    assert (tmp_path / "effect_bri_entryway").read_text() == "1"
    assert lights.effect_is_running("kitchen")
    assert lights.effect_is_running("entryway")
    assert result["effect_groups"] == ["kitchen", "entryway"]
    assert all(item["brightness"] == 1 for item in result["bulbs"])


def test_all_room_with_mixed_all_and_room_effects_honors_specific_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    write_effect(tmp_path, "all", pid=123)
    write_effect(tmp_path, "kitchen", pid=456)
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("effect-controlled bulb should not be created"))

    result = asyncio.run(lights_api.set_group_brightness("all", 200))

    assert (tmp_path / "effect_bri_all").read_text() == "200"
    assert (tmp_path / "effect_bri_kitchen").read_text() == "200"
    assert lights.effect_is_running("all")
    assert lights.effect_is_running("kitchen")
    assert result["effect_groups"] == ["kitchen", "all"]


def test_all_room_with_no_effects_keeps_direct_path(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    bulbs = install_bulbs(monkeypatch, {
        "192.168.86.123": FakeState(rgb=(1, 2, 3)),
        "192.168.86.124": FakeState(rgb=(1, 2, 3)),
        "192.168.86.133": FakeState(colortemp=4200),
        "192.168.86.134": FakeState(colortemp=4200),
    })

    result = asyncio.run(lights_api.set_group_brightness("all", 256))

    assert result["brightness"] == 255
    assert "effect_group" not in result
    assert "effect_groups" not in result
    assert len(bulbs) == 4
    assert all(len(bulb.turn_on_calls) == 1 for bulb in bulbs.values())
    assert all(
        call.kwargs["brightness"] == 255
        for bulb in bulbs.values()
        for call in bulb.turn_on_calls
    )


def test_effect_group_helper_prefers_room_effect_then_all_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(lights, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lights, "_pid_alive", lambda pid: True)
    (tmp_path / "effect_running_all").write_text("bonfire\n123\n")
    (tmp_path / "effect_running_kitchen").write_text("aurora\n456\n")

    assert lights.effect_group_affecting_target("kitchen") == "kitchen"
    assert lights.effect_group_affecting_target("entryway") == "all"
    assert lights.effect_group_affecting_target("all") == "all"


def test_no_effect_preserves_rgb_direct_bulb_path(monkeypatch):
    bulbs = []

    def make_bulb(ip):
        bulb = FakeBulb(ip, FakeState(rgb=(1, 2, 3)))
        bulbs.append(bulb)
        return bulb

    monkeypatch.setattr(lights_api.lights, "effect_group_affecting_target", lambda group: None)
    monkeypatch.setattr(lights_api, "wizlight", make_bulb)
    monkeypatch.setattr(lights_api, "PilotBuilder", FakePilot)

    result = asyncio.run(lights_api.set_group_brightness("kitchen", 0))

    assert result == {
        "room": "kitchen",
        "brightness": 1,
        "bulbs": [
            {"ip": "192.168.86.123", "ok": True, "brightness": 1},
            {"ip": "192.168.86.124", "ok": True, "brightness": 1},
        ],
    }
    assert all(len(bulb.turn_on_calls) == 1 for bulb in bulbs)
    assert all(bulb.closed for bulb in bulbs)
    assert all(
        call.kwargs["rgb"] == (1, 2, 3)
        for bulb in bulbs
        for call in bulb.turn_on_calls
    )


def test_no_effect_preserves_colortemp_and_fallback_paths(monkeypatch):
    states = [FakeState(colortemp=4200), FakeState()]
    bulbs = []

    def make_bulb(ip):
        bulb = FakeBulb(ip, states.pop(0))
        bulbs.append(bulb)
        return bulb

    monkeypatch.setattr(lights_api.lights, "effect_group_affecting_target", lambda group: None)
    monkeypatch.setattr(lights_api, "wizlight", make_bulb)
    monkeypatch.setattr(lights_api, "PilotBuilder", FakePilot)

    asyncio.run(lights_api.set_group_brightness("kitchen", 42))

    pilots = [bulb.turn_on_calls[0] for bulb in bulbs]
    assert pilots[0].kwargs["colortemp"] == 4200
    assert pilots[1].kwargs["colortemp"] == 2700


def test_active_effect_never_evaluates_fallback_direct_path(monkeypatch):
    monkeypatch.setattr(lights_api.lights, "effect_group_affecting_target", lambda group: "all")
    monkeypatch.setattr(lights_api.lights, "save_effect_bri", lambda brightness, group: brightness)
    monkeypatch.setattr(lights_api, "wizlight", lambda ip: AssertionError("fallback path must not run"))

    asyncio.run(lights_api.set_group_brightness("all", 1))
