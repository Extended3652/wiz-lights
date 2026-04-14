#!/home/pi/venvs/wiz/bin/python

import asyncio
import json
import os
import random
import signal
import socket
import subprocess
import sys
from pathlib import Path

from pywizlight import PilotBuilder, wizlight
from pywizlight.exceptions import WizLightConnectionError

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

IPS = [
    "192.168.86.123",  # kitchen 1
    "192.168.86.124",  # kitchen 2
    "192.168.86.133",  # entryway 1
    "192.168.86.134",  # entryway 2
]

# Map bulb IP -> room label (used by dashboard and status output)
ROOM_BY_IP = {
    "192.168.86.123": "KITCHEN",
    "192.168.86.124": "KITCHEN",
    "192.168.86.133": "ENTRYWAY",
    "192.168.86.134": "ENTRYWAY",
}

GROUPS = {
    "all": [
        "192.168.86.123",
        "192.168.86.124",
        "192.168.86.133",
        "192.168.86.134",
    ],
    "kitchen": [
        "192.168.86.123",
        "192.168.86.124",
    ],
    "entryway": [
        "192.168.86.133",
        "192.168.86.134",
    ],
}

GROUP_ALIASES = {
    "kitchen": "kitchen",
    "kit": "kitchen",
    "k": "kitchen",
    "entryway": "entryway",
    "entry": "entryway",
    "e": "entryway",
    "all": "all",
    "a": "all",
}

ACTIVE_GROUP: str | None = None
ACTIVE_IPS: list[str] | None = None


def _set_active_group(group: str | None) -> None:
    global ACTIVE_GROUP, ACTIVE_IPS
    if group is None or group == "all":
        ACTIVE_GROUP = None
        ACTIVE_IPS = None
        return
    if group not in GROUPS:
        raise ValueError(f"Unknown group: {group}")
    ACTIVE_GROUP = group
    ACTIVE_IPS = list(GROUPS[group])


def active_group() -> str | None:
    return ACTIVE_GROUP


def _maybe_consume_group(argv: list[str]) -> tuple[str | None, list[str]]:
    if not argv:
        return None, argv
    tok = argv[0].lower()
    group = GROUP_ALIASES.get(tok)
    if group is None:
        return None, argv
    return group, argv[1:]


def _target_ips() -> list[str]:
    return ACTIVE_IPS if ACTIVE_IPS is not None else IPS


WIZ_PORT = 38899

STATE_DIR = Path("/home/pi/.lights_state")
ALERT_PULSE_TOGGLE = STATE_DIR / "alert_pulse.toggle"
STATE_DIR.mkdir(parents=True, exist_ok=True)

SNAPSHOT_DIR = STATE_DIR / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _snapshot_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip()
    if not safe:
        safe = "default"
    return SNAPSHOT_DIR / f"{safe}.json"


STATE_FILE = STATE_DIR / "last_mode"
EFFECT_FILE = STATE_DIR / "effect_running"
EFFECT_BRI_FILE = STATE_DIR / "effect_bri"

CYCLE_ORDER = [
    "warm",
    "soft",
    "cool",
    "bright",
    "dim",
    "night",
]

BACKGROUND_EFFECTS = {
    "fireplace_ambient",
    "asym_static",
    "embers",
    "bonfire",
    "aurora",
    "cozy_ambient",
    "candle_pair",
    "breathe_soft",
    "focus_wave",
    "dusk_drift",
    "hearth",
    "abyss",
    "storm_distant",
    "police_siren",
}

# --------------------------------------------------
# PRESETS
# - Non-scene presets use PilotBuilder
# - Scene presets use raw UDP setPilot with sceneId
# --------------------------------------------------

PRESETS = {
    "warm": {"brightness": 180, "pilot": PilotBuilder(brightness=180, colortemp=2700)},
    "soft": {"brightness": 140, "pilot": PilotBuilder(brightness=140, colortemp=3000)},
    "cool": {"brightness": 220, "pilot": PilotBuilder(brightness=220, colortemp=5000)},
    "bright": {"brightness": 255, "pilot": PilotBuilder(brightness=255, colortemp=4000)},
    "dim": {"brightness": 80, "pilot": PilotBuilder(brightness=80, colortemp=2700)},
    "night": {"brightness": 30, "pilot": PilotBuilder(brightness=30, colortemp=2200)},
    "red": {"brightness": 200, "pilot": PilotBuilder(brightness=200, rgb=(255, 0, 0))},
    "green": {"brightness": 200, "pilot": PilotBuilder(brightness=200, rgb=(0, 255, 0))},
    "blue": {"brightness": 200, "pilot": PilotBuilder(brightness=200, rgb=(0, 120, 255))},
    "sunset": {"brightness": 120, "pilot": PilotBuilder(brightness=120, rgb=(255, 120, 40))},
    "movie": {"brightness": 60, "pilot": PilotBuilder(brightness=60, rgb=(255, 180, 120))},
    "tiffany_cream": {"brightness": 100, "pilot": PilotBuilder(brightness=100, rgb=(248, 229, 201))},
    "tiffany_honey": {"brightness": 100, "pilot": PilotBuilder(brightness=100, rgb=(241, 193, 89))},
    "tiffany": {"brightness": 160, "duo": ("tiffany_cream", "tiffany_honey")},
}

# --------------------------------------------------
# WiZ scenes / effects via raw sceneId
# Keep these in a separate dict, then merge into PRESETS.
# --------------------------------------------------

SCENE_PRESETS = {
    "ocean": {"brightness": 140, "scene_id": 1},
    "romance": {"brightness": 140, "scene_id": 2},
    "sunset_scene": {"brightness": 140, "scene_id": 3},
    "party": {"brightness": 140, "scene_id": 4},
    "fireplace": {"brightness": 120, "scene_id": 5},
    "cozy": {"brightness": 140, "scene_id": 6},
    "forest": {"brightness": 140, "scene_id": 7},
    "pastel_colors": {"brightness": 140, "scene_id": 8},
    "wake_up": {"brightness": 140, "scene_id": 9},
    "bedtime": {"brightness": 140, "scene_id": 10},
    "warm_white": {"brightness": 180, "scene_id": 11},
    "daylight": {"brightness": 200, "scene_id": 12},
    "cool_white": {"brightness": 200, "scene_id": 13},
    "night_light": {"brightness": 60, "scene_id": 14},
    "focus": {"brightness": 220, "scene_id": 15},
    "relax": {"brightness": 140, "scene_id": 16},
    "true_colors": {"brightness": 140, "scene_id": 17},
    "tv_time": {"brightness": 140, "scene_id": 18},
    "plant_growth": {"brightness": 200, "scene_id": 19},
    "spring": {"brightness": 140, "scene_id": 20},
    "summer": {"brightness": 140, "scene_id": 21},
    "fall": {"brightness": 140, "scene_id": 22},
    "deep_dive": {"brightness": 140, "scene_id": 23},
    "jungle": {"brightness": 140, "scene_id": 24},
    "mojito": {"brightness": 140, "scene_id": 25},
    "club": {"brightness": 140, "scene_id": 26},
    "christmas": {"brightness": 140, "scene_id": 27},
    "halloween": {"brightness": 140, "scene_id": 28},
    "candlelight": {"brightness": 140, "scene_id": 29},
    "golden_white": {"brightness": 160, "scene_id": 30},
    "pulse": {"brightness": 140, "scene_id": 31},
    "steampunk": {"brightness": 140, "scene_id": 32},
}

# Merge scenes into PRESETS so CLI + dashboard see them
PRESETS.update(SCENE_PRESETS)

# Optional: scene ID reverse map for the dashboard status line
SCENE_ID_TO_NAME = {v["scene_id"]: k for k, v in SCENE_PRESETS.items()}

# --------------------------------------------------
# UI COLOR HINTS (used by lights_dashboard.py)
# Single source of truth for preset/effect menu colors.
# --------------------------------------------------

PRESET_RGB_HINTS = {
    # Whites
    "warm": (255, 180, 120),
    "soft": (255, 200, 150),
    "dim": (255, 150, 80),
    "night": (255, 110, 50),
    "cool": (200, 220, 255),
    "bright": (240, 240, 255),

    # Solid RGB presets
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 120, 255),
    "sunset": (255, 120, 40),
    "movie": (255, 180, 120),

    # Duo preset (dashboard alternates the two colors)
    "tiffany": (241, 193, 89),
    "tiffany_cream": (248, 229, 201),
    "tiffany_honey": (241, 193, 89),

    # Scene-ish presets
    "ocean": (0, 120, 255),
    "romance": (255, 0, 120),
    "sunset_scene": (255, 120, 40),
    "party": (255, 0, 255),
    "fireplace": (255, 90, 20),
    "cozy": (255, 170, 90),
    "forest": (0, 170, 90),
    "pastel_colors": (190, 160, 255),
    "wake_up": (255, 210, 140),
    "bedtime": (255, 120, 80),
    "warm_white": (255, 220, 180),
    "daylight": (220, 240, 255),
    "cool_white": (200, 220, 255),
    "night_light": (255, 90, 20),
    "focus": (240, 240, 255),
    "relax": (255, 180, 120),
    "true_colors": (255, 255, 255),
    "tv_time": (180, 140, 255),
    "plant_growth": (120, 255, 120),
    "spring": (140, 255, 180),
    "summer": (255, 230, 120),
    "fall": (255, 140, 60),
    "deep_dive": (0, 80, 255),
    "jungle": (0, 200, 80),
    "mojito": (120, 255, 180),
    "club": (255, 0, 255),
    "christmas": (255, 0, 0),
    "halloween": (255, 80, 0),
    "candlelight": (255, 140, 60),
    "golden_white": (255, 210, 140),
    "pulse": (255, 0, 255),
    "steampunk": (255, 170, 90),
    "diwali": (255, 120, 255),
    "white": (255, 255, 255),
    "alarm": (255, 0, 0),

    # Alert presets
    "alert_white": (255, 255, 255),
    "alert_red": (255, 0, 0),
    "alert_blue": (0, 120, 255),

    # Background effects (menu color hints)
    "embers": (255, 115, 35),
    "hearth": (255, 150, 70),
    "fireplace_ambient": (255, 125, 45),
    "storm_distant": (150, 165, 190),
    "cozy_ambient": (255, 175, 95),
    "candle_pair": (255, 170, 80),
    "asym_static": (255, 215, 170),
    "breathe_soft": (255, 145, 90),
    "focus_wave": (210, 235, 255),
    "dusk_drift": (255, 140, 85),
    "police_siren": (255, 0, 0),
    "abyss": (60, 0, 150),
}

# --------------------------------------------------
# EFFECT STATE (PID + brightness scaling) - PER GROUP
# --------------------------------------------------

def _install_signal_handlers():
    loop = asyncio.get_event_loop()

    async def _cancel():
        clear_effect_running()
        for task in asyncio.all_tasks(loop):
            if task is not asyncio.current_task():
                task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_cancel()))
        except NotImplementedError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _effect_file(group: str | None) -> Path:
    if group and group != "all":
        return STATE_DIR / f"effect_running_{group}"
    return STATE_DIR / "effect_running_all"


def _effect_bri_file(group: str | None) -> Path:
    if group and group != "all":
        return STATE_DIR / f"effect_bri_{group}"
    return STATE_DIR / "effect_bri_all"


def effect_is_running(group: str | None = None) -> bool:
    g = active_group() if group is None else group
    ef = _effect_file(g)

    if not ef.exists():
        return False
    try:
        lines = ef.read_text().splitlines()
        pid = int(lines[1]) if len(lines) > 1 else None
        if pid and _pid_alive(pid):
            return True
    except Exception:
        pass

    ef.unlink(missing_ok=True)
    return False


def set_effect_running(name: str, group: str | None = None) -> None:
    g = active_group() if group is None else group
    ef = _effect_file(g)
    bf = _effect_bri_file(g)

    ef.write_text(f"{name}\n{os.getpid()}\n")
    if not bf.exists():
        bf.write_text("255")


def clear_effect_running(group: str | None = None) -> None:
    g = active_group() if group is None else group
    _effect_file(g).unlink(missing_ok=True)


def load_effect_bri(default: int = 255, group: str | None = None) -> int:
    g = active_group() if group is None else group
    bf = _effect_bri_file(g)
    try:
        if bf.exists():
            v = int(bf.read_text().strip())
            return max(1, min(255, v))
    except Exception:
        pass
    return max(1, min(255, int(default)))


def save_effect_bri(v: int, group: str | None = None) -> int:
    g = active_group() if group is None else group
    bf = _effect_bri_file(g)
    v = max(1, min(255, int(v)))
    bf.write_text(str(v))
    return v


def effect_scale(group: str | None = None) -> float:
    return load_effect_bri(255, group=group) / 255.0


def scale_bri(b: float, group: str | None = None) -> int:
    s = effect_scale(group=group)
    return max(1, min(255, int(round(float(b) * s))))


def stop_running_effect(group: str | None = None) -> None:
    groups_to_stop: list[str | None]
    if group is None:
        groups_to_stop = [None] + [g for g in GROUPS.keys() if g != "all"]
    else:
        groups_to_stop = [group]

    for g in groups_to_stop:
        ef = _effect_file(g)
        if not ef.exists():
            continue
        try:
            lines = ef.read_text().splitlines()
            pid = int(lines[1]) if len(lines) > 1 else None
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        except Exception:
            pass
        ef.unlink(missing_ok=True)


def effect_should_stop(group: str | None = None) -> bool:
    g = active_group() if group is None else group
    ef = _effect_file(g)

    if not ef.exists():
        return True
    try:
        lines = ef.read_text().splitlines()
        pid = int(lines[1]) if len(lines) > 1 else None
        if pid != os.getpid():
            return True
    except Exception:
        return True
    return False


def load_running_effect_name(group: str | None = None) -> str | None:
    g = active_group() if group is None else group
    if not effect_is_running(g):
        return None
    try:
        lines = _effect_file(g).read_text().splitlines()
        name = lines[0].strip() if lines else ""
        return name or None
    except Exception:
        return None

# --------------------------------------------------
# STATE HELPERS
# --------------------------------------------------

def _last_mode_file(group: str | None) -> Path:
    if group:
        return STATE_DIR / f"last_mode_{group}"
    return STATE_DIR / "last_mode"


def save_last_mode(mode: str, group: str | None = None) -> None:
    path = _last_mode_file(group)
    path.write_text(mode)


def load_last_mode(group: str | None = None) -> str | None:
    path = _last_mode_file(group)
    if path.exists():
        return path.read_text().strip()
    return None

# --------------------------------------------------
# CORE HELPERS
# --------------------------------------------------

async def get_bulbs():
    ips = _target_ips()
    return [wizlight(ip) for ip in ips]


async def close_all(bulbs):
    for b in bulbs:
        await b.async_close()


def _brightness_to_dimming_percent(brightness_0_255: int) -> int:
    b = int(brightness_0_255)
    b = max(0, min(255, b))
    pct = int(round((b / 255) * 100))
    return max(1, min(100, pct))


def send_raw_scene(ip: str, scene_id: int, brightness_0_255: int) -> None:
    dimming = _brightness_to_dimming_percent(brightness_0_255)
    payload = {
        "id": 1,
        "method": "setPilot",
        "params": {
            "state": True,
            "sceneId": int(scene_id),
            "dimming": dimming,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (ip, WIZ_PORT))
    finally:
        sock.close()


def send_raw_rgb(ip: str, r: int, g: int, b: int, brightness_0_255: int) -> None:
    dimming = _brightness_to_dimming_percent(brightness_0_255)
    payload = {
        "id": 1,
        "method": "setPilot",
        "params": {
            "state": True,
            "r": int(r), "g": int(g), "b": int(b),
            "dimming": dimming,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (ip, WIZ_PORT))
    finally:
        sock.close()


def send_raw_off(ip: str) -> None:
    payload = {
        "id": 1,
        "method": "setPilot",
        "params": {"state": False},
    }
    data = json.dumps(payload).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (ip, WIZ_PORT))
    finally:
        sock.close()

def send_raw_dim1(ip: str) -> None:
    """Snap a bulb to 1% dimming (used before OFF to avoid slow fade)."""
    payload = {
        "id": 1,
        "method": "setPilot",
        "params": {"state": True, "dimming": 1},
    }
    data = json.dumps(payload).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (ip, WIZ_PORT))
    finally:
        sock.close()

def get_pilot_raw(ip: str, timeout: float = 0.6) -> dict | None:
    """
    Ask the bulb for its current state via WiZ UDP getPilot.
    Returns parsed JSON dict, or None on failure/timeout.
    """
    payload = {"id": 1, "method": "getPilot", "params": {}}
    data = json.dumps(payload).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(float(timeout))
    try:
        sock.sendto(data, (ip, WIZ_PORT))
        resp, _addr = sock.recvfrom(4096)
        return json.loads(resp.decode("utf-8", errors="replace"))
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass

def _validate_mode(mode: str) -> None:
    if mode not in PRESETS:
        raise ValueError(f"Unknown preset: {mode}")


async def _apply_mode_to_bulb(bulb, mode: str) -> None:
    _validate_mode(mode)
    preset = PRESETS[mode]

    if "scene_id" in preset:
        scene_id = preset["scene_id"]
        brightness = int(preset.get("brightness", 140))
        send_raw_scene(bulb.ip, scene_id, brightness)
        print(f"{mode.upper():<12} {bulb.ip} sceneId={scene_id}")
        return

    pilot = preset["pilot"]

    try:
        await bulb.turn_on(pilot)
        print(f"{mode.upper():<12} {bulb.ip}")
    except (WizLightConnectionError, asyncio.TimeoutError) as e:
        print(f"FAIL         {bulb.ip} ({type(e).__name__})")


def launch_background(cmd: str, group: str | None) -> None:
    running = load_running_effect_name(group)
    if running == cmd:
        if group:
            print(f"EFFECT {cmd} already running ({group}), restarting")
        else:
            print(f"EFFECT {cmd} already running, restarting")
        stop_running_effect(group)

    else:
        stop_running_effect(group)

    args = [sys.executable, __file__, "--bg", cmd]
    if group:
        args.append(group)

    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    if group:
        print(f"EFFECT {cmd} started ({group})")
    else:
        print(f"EFFECT {cmd} started")

# --------------------------------------------------
# SNAPSHOTS
# --------------------------------------------------

async def snapshot_save(name: str = "default") -> None:
    bulbs = [wizlight(ip) for ip in IPS]
    try:
        states = await asyncio.gather(*[b.updateState() for b in bulbs])

        data = {"name": name, "bulbs": []}

        for b, st in zip(bulbs, states):
            item = {
                "ip": b.ip,
                "on": bool(st.get_state()),
                "bri": st.get_brightness(),
                "ct": st.get_colortemp(),
                "rgb": st.get_rgb(),
            }
            data["bulbs"].append(item)

        _snapshot_path(name).write_text(json.dumps(data, indent=2))
        print(f"SNAPSHOT     saved {name}")

    finally:
        await close_all(bulbs)


async def snapshot_load(name: str = "default") -> None:
    path = _snapshot_path(name)
    if not path.exists():
        raise SystemExit(f"No snapshot found: {name}")

    data = json.loads(path.read_text())
    bulbs_data = data.get("bulbs", [])

    ip_to_item = {it["ip"]: it for it in bulbs_data if "ip" in it}
    bulbs = [wizlight(ip) for ip in ip_to_item.keys()]

    try:
        tasks = []
        for b in bulbs:
            it = ip_to_item[b.ip]
            if not it.get("on", False):
                tasks.append(asyncio.to_thread(send_raw_off, b.ip))
                continue

            bri = it.get("bri") or 120
            rgb = it.get("rgb")
            ct = it.get("ct")

            rgb_valid = (
                isinstance(rgb, (tuple, list))
                and len(rgb) == 3
                and all(v is not None for v in rgb)
            )

            if rgb_valid:
                tasks.append(b.turn_on(PilotBuilder(brightness=int(bri), rgb=tuple(rgb))))
            elif ct is not None:
                tasks.append(b.turn_on(PilotBuilder(brightness=int(bri), colortemp=int(ct))))
            else:
                tasks.append(b.turn_on(PilotBuilder(brightness=int(bri), colortemp=2700)))

        if tasks:
            await asyncio.gather(*tasks)

        print(f"SNAPSHOT     loaded {name}")

    finally:
        await close_all(bulbs)


def snapshot_list() -> None:
    snaps = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not snaps:
        print("SNAPSHOT     (none)")
        return
    for p in snaps:
        print(f"SNAPSHOT     {p.stem}")

# --------------------------------------------------
# BASIC ACTIONS
# --------------------------------------------------

async def turn_on(mode: str) -> None:
    _validate_mode(mode)
    preset = PRESETS[mode]
    bulbs = await get_bulbs()
    try:
        if "duo" in preset:
            if not bulbs:
                return
            m1, m2 = preset["duo"]
            if len(bulbs) == 1:
                await _apply_mode_to_bulb(bulbs[0], m1)
                return
            await _apply_mode_to_bulb(bulbs[0], m1)
            await _apply_mode_to_bulb(bulbs[1], m2)
            return

        await asyncio.gather(*[_apply_mode_to_bulb(bulb, mode) for bulb in bulbs])
    finally:
        await close_all(bulbs)


async def turn_on_b1(mode: str) -> None:
    bulbs = await get_bulbs()
    try:
        if bulbs:
            await _apply_mode_to_bulb(bulbs[0], mode)
    finally:
        await close_all(bulbs)


async def turn_on_b2(mode: str) -> None:
    bulbs = await get_bulbs()
    try:
        if len(bulbs) < 2:
            raise RuntimeError("Need at least 2 bulbs for b2")
        await _apply_mode_to_bulb(bulbs[1], mode)
    finally:
        await close_all(bulbs)


async def turn_duo(mode_b1: str, mode_b2: str) -> None:
    bulbs = await get_bulbs()
    try:
        if len(bulbs) < 2:
            raise RuntimeError("Need at least 2 bulbs for duo")
        await _apply_mode_to_bulb(bulbs[0], mode_b1)
        await _apply_mode_to_bulb(bulbs[1], mode_b2)
    finally:
        await close_all(bulbs)


async def turn_off() -> None:
    bulbs = await get_bulbs()
    try:
        # Group bulbs by room to keep visual timing consistent
        rooms: dict[str, list] = {}
        for b in bulbs:
            label = ROOM_BY_IP.get(b.ip, "UNKNOWN")
            rooms.setdefault(label, []).append(b)

        async def _burst_off(room_bulbs: list) -> None:
            # Quick OFF bursts (helps with UDP misses)
            for _ in range(4):
                await asyncio.gather(*[
                    asyncio.to_thread(send_raw_off, b.ip)
                    for b in room_bulbs
                ])
                await asyncio.sleep(0.04)

        async def _snap_dim_1(room_bulbs: list) -> None:
            # Some bulbs do a slow fade on OFF. This "snaps" them to 1% first.
            await asyncio.gather(*[
                asyncio.to_thread(send_raw_dim1, b.ip)
                for b in room_bulbs
            ])

        async def _hard_off(room_bulbs: list) -> None:
            # Snap then OFF bursts
            await _snap_dim_1(room_bulbs)
            await asyncio.sleep(0.05)
            await _burst_off(room_bulbs)

        # Run each room in parallel so entryway matches kitchen timing
        tasks = [asyncio.create_task(_hard_off(room_bulbs)) for room_bulbs in rooms.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        await close_all(bulbs)


async def show_status() -> None:
    bulbs = await get_bulbs()
    try:
        states = await asyncio.gather(*[b.updateState() for b in bulbs])

        running = load_running_effect_name()
        last = load_last_mode(active_group())

        on_bris = []
        for st in states:
            if st.get_state():
                bri = st.get_brightness()
                if bri is not None:
                    on_bris.append(int(bri))

        avg_bri = int(round(sum(on_bris) / len(on_bris))) if on_bris else None

        if running:
            s = load_effect_bri(255)
            pct = int(round((s / 255.0) * 100))
            est_raw = None
            if avg_bri is not None and s > 0:
                est_raw = int(round(avg_bri / (s / 255.0)))

            if avg_bri is not None and est_raw is not None:
                print(f"EFFECT       {running} scale={s}/255 ({pct}%) avg_bri={avg_bri} est_raw={est_raw}")
            else:
                print(f"EFFECT       {running} scale={s}/255 ({pct}%)")
        else:
            if last:
                if last in BACKGROUND_EFFECTS:
                    print(f"MODE         {last} (not running)")
                    print(f"HINT         run: lights {last}")
                else:
                    print(f"MODE         {last}")
            else:
                print("MODE         (none)")

        for bulb, state in zip(bulbs, states):
            if not state.get_state():
                print(f"OFF          {bulb.ip}")
                continue

            bri = state.get_brightness()
            rgb = state.get_rgb()
            ct = state.get_colortemp()

            rgb_valid = (
                isinstance(rgb, (tuple, list))
                and len(rgb) == 3
                and all(v is not None for v in rgb)
            )

            if rgb_valid:
                print(f"ON           {bulb.ip} bri={bri} rgb={rgb}")
            elif ct is not None:
                print(f"ON           {bulb.ip} bri={bri} ct={ct}")
            else:
                print(f"ON           {bulb.ip} bri={bri}")
    finally:
        await close_all(bulbs)


async def dim_adjust(target: str, delta: int) -> None:
    bulbs = await get_bulbs()
    try:
        if not bulbs:
            return

        if target == "B1":
            bulbs = bulbs[:1]
        elif target == "B2":
            if len(bulbs) < 2:
                raise RuntimeError("Need at least 2 bulbs for B2")
            bulbs = [bulbs[1]]

        delta = int(delta)

        for b in bulbs:
            st = await b.updateState()
            cur_bri = st.get_brightness() or 120
            new_bri = max(1, min(255, int(cur_bri) + delta))

            last = load_last_mode(active_group())
            if last and last in PRESETS and "scene_id" in PRESETS[last]:
                scene_id = PRESETS[last]["scene_id"]
                send_raw_scene(b.ip, scene_id, new_bri)
                print(f"DIM_SCENE    {b.ip} sceneId={scene_id} bri={new_bri}")
                continue

            rgb = st.get_rgb()
            ct = st.get_colortemp()

            rgb_valid = (
                isinstance(rgb, (tuple, list))
                and len(rgb) == 3
                and all(v is not None for v in rgb)
            )

            if rgb_valid:
                pilot = PilotBuilder(brightness=new_bri, rgb=tuple(rgb))
            elif ct is not None:
                pilot = PilotBuilder(brightness=new_bri, colortemp=int(ct))
            else:
                pilot = PilotBuilder(brightness=new_bri, colortemp=2700)

            await b.turn_on(pilot)
            print(f"DIM          {b.ip} bri={new_bri}")

    finally:
        await close_all(bulbs)

# --------------------------------------------------
# FADE
# --------------------------------------------------

async def fade_to(mode: str, seconds: float) -> None:
    bulbs = await get_bulbs()
    _validate_mode(mode)
    target = PRESETS[mode]

    steps = max(int(float(seconds) * 10), 1)
    delay = float(seconds) / steps
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    try:
        if "duo" in target:
            if not bulbs:
                return
            m1, m2 = target["duo"]

            if len(bulbs) == 1:
                t1 = PRESETS[m1]
                target_bri1 = int(t1.get("brightness", 140))
                st1 = await bulbs[0].updateState()
                start_bri1 = st1.get_brightness() or 0

                for i in range(steps):
                    level = (i + 1) / steps
                    bri1 = int(start_bri1 + (target_bri1 - start_bri1) * level)
                    await bulbs[0].turn_on(PilotBuilder(brightness=bri1))
                    next_tick = start_time + (i + 1) * delay
                    await asyncio.sleep(max(0, next_tick - loop.time()))

                await _apply_mode_to_bulb(bulbs[0], m1)
                print(f"FADE         {bulbs[0].ip} -> {mode}")
                return

            t1 = PRESETS[m1]
            t2 = PRESETS[m2]
            target_bri1 = int(t1.get("brightness", 140))
            target_bri2 = int(t2.get("brightness", 140))

            st1, st2 = await asyncio.gather(bulbs[0].updateState(), bulbs[1].updateState())
            start_bri1 = st1.get_brightness() or 0
            start_bri2 = st2.get_brightness() or 0

            for i in range(steps):
                level = (i + 1) / steps
                bri1 = int(start_bri1 + (target_bri1 - start_bri1) * level)
                bri2 = int(start_bri2 + (target_bri2 - start_bri2) * level)

                await asyncio.gather(
                    bulbs[0].turn_on(PilotBuilder(brightness=bri1)),
                    bulbs[1].turn_on(PilotBuilder(brightness=bri2)),
                )

                next_tick = start_time + (i + 1) * delay
                await asyncio.sleep(max(0, next_tick - loop.time()))

            await _apply_mode_to_bulb(bulbs[0], m1)
            await _apply_mode_to_bulb(bulbs[1], m2)
            print(f"FADE         {bulbs[0].ip} {bulbs[1].ip} -> {mode}")
            return

        if "scene_id" in target:
            target_bri = int(target.get("brightness", 140))
            target_scene_id = int(target["scene_id"])
        else:
            target_bri = int(target.get("brightness", 140))
            target_scene_id = None

        states = await asyncio.gather(*[bulb.updateState() for bulb in bulbs])
        start_bris = [s.get_brightness() or 0 for s in states]

        for i in range(steps):
            level = (i + 1) / steps
            tasks = []
            for bulb, start_bri in zip(bulbs, start_bris):
                bri = int(start_bri + (target_bri - start_bri) * level)
                tasks.append(bulb.turn_on(PilotBuilder(brightness=bri)))
            await asyncio.gather(*tasks)

            next_tick = start_time + (i + 1) * delay
            await asyncio.sleep(max(0, next_tick - loop.time()))

        if target_scene_id is not None:
            for bulb in bulbs:
                send_raw_scene(bulb.ip, target_scene_id, target_bri)
        else:
            pilot = target.get("pilot")
            if pilot:
                await asyncio.gather(*[bulb.turn_on(pilot) for bulb in bulbs])
            else:
                await asyncio.gather(*[bulb.turn_on(PilotBuilder(brightness=target_bri)) for bulb in bulbs])

        for bulb in bulbs:
            print(f"FADE         {bulb.ip} -> {mode}")

    finally:
        await close_all(bulbs)

# --------------------------------------------------
# ALERTS / EFFECTS (foreground timers)
# --------------------------------------------------

async def alert_police(seconds: float = 15, interval: float = 0.4) -> None:
    bulbs = await get_bulbs()
    end_time = asyncio.get_event_loop().time() + float(seconds)
    set_effect_running("alert_police")

    try:
        while asyncio.get_event_loop().time() < end_time:
            bri = load_effect_bri(255)
            red = PilotBuilder(brightness=bri, rgb=(255, 0, 0))
            blue = PilotBuilder(brightness=bri, rgb=(0, 120, 255))
            await asyncio.gather(*[b.turn_on(red) for b in bulbs])
            await asyncio.sleep(float(interval))
            await asyncio.gather(*[b.turn_on(blue) for b in bulbs])
            await asyncio.sleep(float(interval))

        bri = load_effect_bri(255)
        white = PilotBuilder(brightness=bri, colortemp=6500)
        await asyncio.gather(*[b.turn_on(white) for b in bulbs])
        for b in bulbs:
            print(f"POLICE       {b.ip}")

    finally:
        clear_effect_running()
        try:
            ALERT_PULSE_TOGGLE.unlink()
        except FileNotFoundError:
            pass
        await close_all(bulbs)


async def alert_pulse(seconds: float = 15) -> None:
    bulbs = await get_bulbs()
    end_time = asyncio.get_event_loop().time() + float(seconds)
    set_effect_running("alert_pulse")

    try:
        while asyncio.get_event_loop().time() < end_time:
            await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(40), colortemp=4000)) for b in bulbs])
            await asyncio.sleep(0.6)
            await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(255), colortemp=4000)) for b in bulbs])
            await asyncio.sleep(0.6)

        await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(160), colortemp=4000)) for b in bulbs])
        for b in bulbs:
            print(f"PULSE        {b.ip}")

    finally:
        clear_effect_running()
        await close_all(bulbs)

# --------------------------------------------------
# LOOPING EFFECTS (background via --bg)
# --------------------------------------------------

async def police_siren(seconds: float = 3600, interval: float = 0.25) -> None:
    bulbs = await get_bulbs()
    set_effect_running("police_siren")
    end_time = asyncio.get_event_loop().time() + float(seconds)

    try:
        if not bulbs:
            return

        if len(bulbs) == 1:
            b = bulbs[0]
            while (asyncio.get_event_loop().time() < end_time) and (not effect_should_stop()):
                bri = load_effect_bri(255)
                await b.turn_on(PilotBuilder(brightness=bri, rgb=(255, 0, 0)))
                await asyncio.sleep(float(interval))
                await b.turn_on(PilotBuilder(brightness=bri, rgb=(0, 120, 255)))
                await asyncio.sleep(float(interval))

            bri = load_effect_bri(255)
            await b.turn_on(PilotBuilder(brightness=bri, colortemp=6500))
            print(f"POLICE_SIREN  {b.ip}")
            return

        b1, b2 = bulbs[0], bulbs[1]
        while (asyncio.get_event_loop().time() < end_time) and (not effect_should_stop()):
            bri = load_effect_bri(255)
            red = PilotBuilder(brightness=bri, rgb=(255, 0, 0))
            blue = PilotBuilder(brightness=bri, rgb=(0, 120, 255))

            await asyncio.gather(b1.turn_on(red), b2.turn_on(blue))
            await asyncio.sleep(float(interval))
            await asyncio.gather(b1.turn_on(blue), b2.turn_on(red))
            await asyncio.sleep(float(interval))

        bri = load_effect_bri(255)
        white = PilotBuilder(brightness=bri, colortemp=6500)
        await asyncio.gather(b1.turn_on(white), b2.turn_on(white))
        print(f"POLICE_SIREN  {b1.ip} {b2.ip}")

    finally:
        clear_effect_running()
        await close_all(bulbs)


def _fireplace_rand_bri(base_bri: int = 120, bri_jitter: int = 18) -> int:
    raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
    x = int(scale_bri(raw))
    return max(10, min(255, x))


def _quantize_step(v: int, step: int = 6) -> int:
    v = int(v)
    return int(round(v / step) * step)


async def _fireplace_organic_single(bulb, scene_id: int, base_bri: int, bri_jitter: int, min_wait: float, max_wait: float) -> None:
    send_raw_scene(bulb.ip, scene_id, int(base_bri))
    await asyncio.sleep(0.2)

    while not effect_should_stop():
        send_raw_scene(bulb.ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
        await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))


async def _fireplace_async_single(bulb, scene_id: int, base_bri: int, bri_jitter: int, min_wait: float, max_wait: float) -> None:
    send_raw_scene(bulb.ip, scene_id, int(base_bri))
    await asyncio.sleep(0.2)

    while not effect_should_stop():
        raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
        raw = _quantize_step(raw, step=7)
        raw = max(10, min(255, int(scale_bri(raw))))
        send_raw_scene(bulb.ip, scene_id, raw)
        await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))


async def hearth() -> None:
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("hearth requires 2 bulbs")

    set_effect_running("hearth")
    print("HEARTH        background start")

    scene_id = 5
    b1, b2 = bulbs[0], bulbs[1]

    try:
        await asyncio.gather(
            _fireplace_organic_single(b1, scene_id, base_bri=130, bri_jitter=18, min_wait=5.5, max_wait=18.0),
            _fireplace_async_single(b2, scene_id, base_bri=95, bri_jitter=14, min_wait=0.35, max_wait=1.6),
        )
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def embers():
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("embers requires 2 bulbs")

    set_effect_running("embers")

    try:
        await fireplace_organic(managed=False)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def bonfire():
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("bonfire requires 2 bulbs")

    set_effect_running("bonfire")

    try:
        await bonfire_organic(managed=False)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def bonfire_organic(min_wait=2, max_wait=9, base_bri=145, bri_jitter=28, managed=True):
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("bonfire_organic requires 2 bulbs")

    scene_id = 5
    if managed:
        set_effect_running("bonfire_organic")
    print("BONFIRE_ORG   background start")

    try:
        for b in bulbs:
            send_raw_scene(b.ip, scene_id, int(base_bri))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice([0, 1])
            send_raw_scene(bulbs[idx].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
            print(f"BONFIRE_ORG   reseed {bulbs[idx].ip}")

            if random.random() < 0.55:
                other = 1 - idx
                delay = random.uniform(0.05, 0.5)
                await asyncio.sleep(delay)
                send_raw_scene(bulbs[other].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
                print(f"BONFIRE_ORG   reseed {bulbs[other].ip} after {delay:.2f}s")

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
            await close_all(bulbs)


async def fireplace_organic(min_wait=6, max_wait=22, base_bri=120, bri_jitter=18, managed=True):
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("fireplace_organic requires 2 bulbs")

    scene_id = 5
    if managed:
        set_effect_running("fireplace_organic")
    print("FIREPLACE_ORG  background start")

    try:
        for b in bulbs:
            send_raw_scene(b.ip, scene_id, int(base_bri))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice([0, 1])
            send_raw_scene(bulbs[idx].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
            print(f"FIREPLACE_ORG  reseed {bulbs[idx].ip}")

            if random.random() < 0.35:
                other = 1 - idx
                delay = random.uniform(0.15, 1.2)
                await asyncio.sleep(delay)
                send_raw_scene(bulbs[other].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
                print(f"FIREPLACE_ORG  reseed {bulbs[other].ip} after {delay:.2f}s")

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
            await close_all(bulbs)


async def fireplace_ambient(
    base_bri: int = 120,
    bri_jitter: int = 18,
    ambient_scale: float = 0.65,
    min_wait: float = 0.15,
    max_wait: float = 1.2,
    managed: bool = True,
    effect_name: str = "fireplace_ambient",
) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("fireplace_ambient requires exactly 2 bulbs")

    scene_id = 5

    if managed:
        set_effect_running(effect_name)
    label = effect_name.upper()[:12].ljust(12)
    print(f"{label} background start")

    try:
        for b in bulbs:
            send_raw_scene(b.ip, scene_id, int(base_bri))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice([0, 1])
            src = bulbs[idx]
            amb = bulbs[1 - idx]

            bri0 = _fireplace_rand_bri(base_bri, bri_jitter)
            send_raw_scene(src.ip, scene_id, bri0)

            if random.random() < 0.35:
                delay = random.uniform(0.15, 1.0)
                await asyncio.sleep(delay)
                bri1 = int(max(10, min(255, int(bri0 * float(ambient_scale)))))
                send_raw_scene(amb.ip, scene_id, bri1)

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
        await close_all(bulbs)


async def asym_static() -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("asym_static requires exactly 2 bulbs")

    set_effect_running("asym_static")
    print("ASYM_STATIC   background start")

    try:
        while not effect_should_stop():
            await bulbs[0].turn_on(PilotBuilder(brightness=scale_bri(150), colortemp=3500))
            await bulbs[1].turn_on(PilotBuilder(brightness=scale_bri(80), colortemp=3500))
            await asyncio.sleep(0.6)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def cozy_ambient(base_bri: int = 110, delta: int = 18, min_wait: float = 20, max_wait: float = 60) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("cozy_ambient requires exactly 2 bulbs")

    ct = 2700
    set_effect_running("cozy_ambient")
    print("COZY_AMBIENT  background start")

    try:
        await bulbs[0].turn_on(PilotBuilder(brightness=scale_bri(base_bri + delta), colortemp=ct))
        await bulbs[1].turn_on(PilotBuilder(brightness=scale_bri(base_bri - delta), colortemp=ct))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            lead = random.choice([0, 1])
            follow = 1 - lead
            drift = random.randint(-8, 8)

            await bulbs[lead].turn_on(PilotBuilder(brightness=scale_bri(base_bri + delta + drift), colortemp=ct))
            await bulbs[follow].turn_on(PilotBuilder(brightness=scale_bri(base_bri - delta), colortemp=ct))

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def candle_pair(base_bri: int = 80, jitter: int = 10, min_wait: float = 2.5, max_wait: float = 6.0) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("candle_pair requires exactly 2 bulbs")

    rgb = (255, 170, 80)
    set_effect_running("candle_pair")
    print("CANDLE_PAIR   background start")

    try:
        for b in bulbs:
            await b.turn_on(PilotBuilder(brightness=scale_bri(base_bri), rgb=rgb))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice([0, 1])
            bri = base_bri + random.randint(-jitter, jitter)
            await bulbs[idx].turn_on(PilotBuilder(brightness=scale_bri(bri), rgb=rgb))

            if random.random() < 0.3:
                await asyncio.sleep(random.uniform(0.2, 0.6))
                await bulbs[1 - idx].turn_on(PilotBuilder(brightness=scale_bri(int(bri * 0.6)), rgb=rgb))

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def _apply_brightness_all(bulbs, bri: int, ct: int = 2700) -> None:
    bri = int(max(1, min(255, bri)))
    bri = scale_bri(bri) if effect_is_running() else bri
    await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=bri, colortemp=ct)) for b in bulbs])


async def _ramp(bulbs, start_bri: int, end_bri: int, seconds: float, ct: int = 2700, hz: int = 20) -> None:
    steps = max(int(float(seconds) * int(hz)), 1)
    delay = float(seconds) / steps
    loop = asyncio.get_event_loop()
    t0 = loop.time()

    for i in range(steps):
        level = (i + 1) / steps
        bri = int(start_bri + (end_bri - start_bri) * level)
        await _apply_brightness_all(bulbs, bri, ct=ct)
        next_tick = t0 + (i + 1) * delay
        await asyncio.sleep(max(0, next_tick - loop.time()))


async def breathe_soft(low: int = 60, high: int = 120, cycle: float = 16) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("breathe_soft requires exactly 2 bulbs")

    ct = 2700
    set_effect_running("breathe_soft")
    print("BREATHE_SOFT  background start")

    try:
        while not effect_should_stop():
            await _ramp([bulbs[0]], low, high, cycle / 2, ct=ct)
            await _ramp([bulbs[1]], high, low, cycle / 2, ct=ct)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def focus_wave(low: int = 90, high: int = 140, ct: int = 4000, cycle: float = 14) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("focus_wave requires exactly 2 bulbs")

    set_effect_running("focus_wave")
    print("FOCUS_WAVE    background start")

    try:
        while not effect_should_stop():
            await _ramp([bulbs[0]], low, high, cycle / 2, ct=ct)
            await _ramp([bulbs[1]], high, low, cycle / 2, ct=ct)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def dusk_drift(start_ct: int = 4200, end_ct: int = 2400, base_bri: int = 120, step_ct: int = 40, step_time: float = 1.0) -> None:
    bulbs = await get_bulbs()
    if len(bulbs) != 2:
        raise RuntimeError("dusk_drift requires exactly 2 bulbs")

    set_effect_running("dusk_drift")
    print("DUSK_DRIFT    background start")

    try:
        ct = int(start_ct)
        while (ct >= int(end_ct)) and (not effect_should_stop()):
            await asyncio.gather(*[
                b.turn_on(PilotBuilder(brightness=scale_bri(base_bri), colortemp=int(ct)))
                for b in bulbs
            ])
            await asyncio.sleep(float(step_time))
            ct -= int(step_ct)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def storm_distant(base_bri: int = 70) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running("storm_distant")
    print("STORM_DISTANT background start")

    try:
        for b in bulbs:
            await b.turn_on(PilotBuilder(brightness=scale_bri(base_bri), colortemp=3500))
        await asyncio.sleep(0.5)

        while not effect_should_stop():
            await asyncio.sleep(random.uniform(6, 16))

            flash_bri = scale_bri(220)
            await asyncio.gather(*[
                b.turn_on(PilotBuilder(brightness=flash_bri, colortemp=6500))
                for b in bulbs
            ])
            await asyncio.sleep(random.uniform(0.08, 0.15))

            await asyncio.gather(*[
                b.turn_on(PilotBuilder(brightness=scale_bri(base_bri), colortemp=3500))
                for b in bulbs
            ])

    finally:
        clear_effect_running()
        await close_all(bulbs)

def _aurora_rand_rgb() -> tuple[int, int, int]:
    roll = random.random()
    if roll < 0.50:
        # Green curtain
        return (random.randint(0, 15), random.randint(160, 255), random.randint(20, 80))
    if roll < 0.85:
        # Teal/blue-green ribbon
        return (random.randint(0, 10), random.randint(100, 200), random.randint(80, 180))
    # Purple/violet shimmer
    return (random.randint(80, 160), random.randint(0, 40), random.randint(160, 230))


def _aurora_rand_bri(base_bri: int = 70, bri_jitter: int = 20) -> int:
    return max(20, min(110, base_bri + random.randint(-bri_jitter, bri_jitter)))


async def aurora(min_wait: float = 8, max_wait: float = 28, base_bri: int = 70, bri_jitter: int = 20):
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("aurora requires 2 bulbs")

    set_effect_running("aurora")
    print("AURORA        background start")

    try:
        for b in bulbs:
            await b.turn_on(PilotBuilder(brightness=scale_bri(_aurora_rand_bri(base_bri, bri_jitter)), rgb=_aurora_rand_rgb()))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice([0, 1])
            await bulbs[idx].turn_on(PilotBuilder(brightness=scale_bri(_aurora_rand_bri(base_bri, bri_jitter)), rgb=_aurora_rand_rgb()))
            print(f"AURORA        reseed {bulbs[idx].ip}")

            if random.random() < 0.40:
                other = 1 - idx
                delay = random.uniform(0.5, 2.5)
                await asyncio.sleep(delay)
                await bulbs[other].turn_on(PilotBuilder(brightness=scale_bri(_aurora_rand_bri(base_bri, bri_jitter)), rgb=_aurora_rand_rgb()))
                print(f"AURORA        reseed {bulbs[other].ip} after {delay:.2f}s")

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        clear_effect_running()
        await close_all(bulbs)


def _clamp(n: int, lo: int, hi: int) -> int:
    return lo if n < lo else hi if n > hi else n


def _deep_ocean_rand_rgb() -> tuple[int, int, int]:
    """
    Abyss palette — deep blue/purple flame, no green.
    """
    roll = random.random()

    # Bright violet flare
    if roll < 0.08:
        return (random.randint(90, 140), random.randint(0, 8), random.randint(200, 255))

    # Deep violet — dominant
    if roll < 0.50:
        return (random.randint(40, 85), random.randint(0, 6), random.randint(120, 185))

    # Midnight indigo — bluer
    if roll < 0.75:
        return (random.randint(15, 45), random.randint(0, 5), random.randint(160, 230))

    # Dark plum — warmer
    if roll < 0.90:
        return (random.randint(60, 100), random.randint(0, 8), random.randint(80, 135))

    # Deep cobalt — cold accent
    return (random.randint(5, 20), random.randint(0, 10), random.randint(140, 200))



async def deep_ocean_organic(min_wait=6, max_wait=22, base_bri=55, bri_jitter=20, managed=True):
    """Abyss effect — same architecture as embers, purple/blue flame colors."""
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("abyss requires 2 bulbs")

    if managed:
        set_effect_running("abyss")
    print("ABYSS         background start")

    def _rand_bri():
        raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
        return max(10, min(255, int(scale_bri(raw))))

    def _send(b, rgb, bri):
        send_raw_rgb(b.ip, rgb[0], rgb[1], rgb[2], bri)

    try:
        # Initialize all bulbs
        for b in bulbs:
            _send(b, _deep_ocean_rand_rgb(), _rand_bri())
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.choice(range(len(bulbs)))
            _send(bulbs[idx], _deep_ocean_rand_rgb(), _rand_bri())

            if random.random() < 0.55:
                other = (idx + 1) % len(bulbs)
                delay = random.uniform(0.05, 0.5)
                await asyncio.sleep(delay)
                _send(bulbs[other], _deep_ocean_rand_rgb(), _rand_bri())

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
        await close_all(bulbs)


# --------------------------------------------------
# BACKGROUND DISPATCH
# --------------------------------------------------

async def run_background(cmd: str, args: list[str]) -> None:
    _install_signal_handlers()

    if cmd == "fireplace_ambient":
        await fireplace_ambient()
        return

    if cmd == "asym_static":
        await asym_static()
        return

    if cmd == "cozy_ambient":
        await cozy_ambient()
        return

    if cmd == "candle_pair":
        await candle_pair()
        return

    if cmd == "breathe_soft":
        await breathe_soft()
        return

    if cmd == "focus_wave":
        await focus_wave()
        return

    if cmd == "dusk_drift":
        await dusk_drift()
        return

    if cmd == "storm_distant":
        await storm_distant()
        return

    if cmd == "embers":
        await embers()
        save_last_mode("embers", active_group())
        return

    if cmd == "bonfire":
        await bonfire()
        save_last_mode("bonfire", active_group())
        return

    if cmd == "aurora":
        await aurora()
        save_last_mode("aurora", active_group())
        return

    if cmd == "hearth":
        await fireplace_ambient(managed=True, effect_name="hearth")
        save_last_mode("hearth", active_group())
        return

    if cmd == "police_siren":
        await police_siren()
        save_last_mode("police_siren", active_group())
        return

    if cmd == "abyss":
        await deep_ocean_organic()
        save_last_mode("abyss", active_group())
        return

    if cmd == "alert_police":
        secs = float(args[0]) if len(args) > 0 else 15.0
        stop_running_effect(active_group())
        await alert_police(seconds=secs)
        return

    if cmd == "alert_pulse":
        secs = float(args[0]) if len(args) > 0 else 15.0

        if ALERT_PULSE_TOGGLE.exists():
            try:
                ALERT_PULSE_TOGGLE.unlink()
            except FileNotFoundError:
                pass
            stop_running_effect(active_group())
            print("ALERT_PULSE   stopped")
            return

        ALERT_PULSE_TOGGLE.write_text("1")
        stop_running_effect(active_group())
        await alert_pulse(seconds=secs)
        return

    raise RuntimeError(f"Unknown background effect: {cmd}")

# --------------------------------------------------
# MAIN CLI
# --------------------------------------------------

def print_help() -> None:
    print("Usage: lights [group] <command>")
    print("")
    print("Groups:")
    print("  kitchen | kit | k")
    print("  entryway | entry | e")
    print("  all | a")
    print("")
    print("Commands:")
    print("  status              Show current state")
    print("  dash | dashboard     Live-updating status view")
    print("  on | off | toggle | stop")
    print("  dim <delta>")
    print("  dim <B1|B2> <delta>")
    print("  alert [seconds]       Pulse alert (toggle on/off)")
    print("  alert_pulse [seconds] Pulse alert (toggle on/off)")
    print("  alert_police [seconds] Police-style alert")
    print("  fade <preset> <seconds>")
    print("  b1 <preset> | b2 <preset> | duo <preset1> <preset2>")
    print("  snapshot save [name] | snapshot load [name] | snapshot list")
    print("")
    print("Background effects:")
    for name in sorted(BACKGROUND_EFFECTS):
        print(f"  {name}")
    print("")
    print("Static presets:")
    for name in sorted(PRESETS.keys()):
        if name not in BACKGROUND_EFFECTS:
            print(f"  {name}")

async def dashboard_loop(interval: float = 1.0) -> None:
    try:
        while True:
            # Clear screen and move cursor home
            print("\033[2J\033[H", end="")
            if active_group():
                print(f"GROUP        {active_group()}")
            await show_status()
            print("")
            print("Ctrl-C to exit")
            await asyncio.sleep(float(interval))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return

async def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("Usage: lights <command>")

    # Background runner entrypoint (child process)
    if argv[0] == "--bg":
        if len(argv) < 2:
            raise SystemExit("Usage: lights --bg <effect> [group]")
        bg_cmd = argv[1]
        rest = argv[2:]
        group, rest = _maybe_consume_group(rest)
        _set_active_group(group)
        await run_background(bg_cmd, rest)
        return

    # Optional group prefix (normal CLI)
    group, argv = _maybe_consume_group(argv)
    _set_active_group(group)
    group_for_bg = group if group and group != "all" else None

    # If user typed only a group, show status for that group
    if not argv:
        await show_status()
        return

    cmd = argv[0]

    if cmd in {"help", "-h", "--help", "?"}:
        print_help()
        return

    if cmd in {"dash", "dashboard"}:
        # Launch the curses dashboard UI (lights_dashboard.py)
        from pathlib import Path

        # Important: resolve() so we follow /home/pi/bin/lights -> /home/pi/projects/wiz_lights/lights.py
        here = Path(__file__).resolve().parent
        dash_py = here / "lights_dashboard.py"

        if not dash_py.exists():
            print(f"ERROR        dashboard script not found: {dash_py}")
            raise SystemExit(2)

        os.execv(sys.executable, [sys.executable, str(dash_py)])

    if cmd == "fireplace_organic":
        raise SystemExit("fireplace_organic is now internal. Use: lights embers")

    # ---------------- basic ----------------
    if cmd == "on":
        stop_running_effect(active_group())

        mode = load_last_mode(active_group()) or "golden_white"

        if mode in BACKGROUND_EFFECTS:
            launch_background(mode, group_for_bg)
            return

        await turn_on(mode)
        return

    if cmd == "snapshot":
        sub = argv[1] if len(argv) > 1 else "list"
        name = argv[2] if len(argv) > 2 else "default"

        if sub == "save":
            await snapshot_save(name)
            return
        if sub == "load":
            await snapshot_load(name)
            return
        if sub == "list":
            snapshot_list()
            return

        raise SystemExit("Usage: lights snapshot save [name] | load [name] | list")

    if cmd == "off":
        stop_running_effect(active_group())
        await turn_off()
        save_last_mode("golden_white", active_group())
        return

    if cmd == "toggle":
        # Optional mode override: lights kitchen toggle cozy
        toggle_mode = argv[1] if len(argv) > 1 else None

        # Query bulbs using robust UDP helper (returns None on timeout
        # instead of throwing).  Check all bulbs so one unreachable
        # bulb doesn't break the toggle.
        ips = _target_ips()
        any_on = False
        for ip in ips:
            result = await asyncio.to_thread(get_pilot_raw, ip, 0.6)
            if result is not None:
                state = result.get("result", {}).get("state", False)
                if state:
                    any_on = True
                    break

        if any_on:
            stop_running_effect(active_group())
            await turn_off()
        else:
            mode = toggle_mode or load_last_mode(active_group()) or "golden_white"
            if mode in BACKGROUND_EFFECTS:
                launch_background(mode, group_for_bg)
            else:
                await turn_on(mode)
        return

    if cmd == "status":
        await show_status()
        return

    if cmd == "stop":
        try:
            ALERT_PULSE_TOGGLE.unlink()
        except FileNotFoundError:
            pass
        stop_running_effect(active_group())
        return

    # ---------------- alerts ----------------
    if cmd == "alert":
        secs = float(argv[1]) if len(argv) > 1 else 15.0

        # Toggle behavior: if already on, turn it off.
        if ALERT_PULSE_TOGGLE.exists():
            try:
                ALERT_PULSE_TOGGLE.unlink()
            except FileNotFoundError:
                pass
            stop_running_effect(active_group())
            print("ALERT_PULSE   stopped")
            return

        # Otherwise start it
        ALERT_PULSE_TOGGLE.write_text("1")
        stop_running_effect(active_group())
        await alert_pulse(seconds=secs)
        return

    if cmd == "alert_police":
        secs = float(argv[1]) if len(argv) > 1 else 15.0
        stop_running_effect(active_group())
        await alert_police(seconds=secs)
        return

    if cmd == "alert_pulse":
        secs = float(argv[1]) if len(argv) > 1 else 15.0

        # Toggle behavior: if already on, turn it off.
        if ALERT_PULSE_TOGGLE.exists():
            try:
                ALERT_PULSE_TOGGLE.unlink()
            except FileNotFoundError:
                pass
            stop_running_effect(active_group())
            print("ALERT_PULSE   stopped")
            return

        # Otherwise start it
        ALERT_PULSE_TOGGLE.write_text("1")
        stop_running_effect(active_group())
        await alert_pulse(seconds=secs)
        return


    # ---------------- dim ----------------
    if cmd == "dim":
        if len(argv) == 2:
            delta = int(argv[1])
            if effect_is_running():
                cur = load_effect_bri(255)
                new = save_effect_bri(cur + delta)
                print(f"EFFECT_DIM    bri={new}")
                return
            await dim_adjust("ALL", delta)
            return

        if len(argv) == 3:
            raw_target = argv[1].strip().lower()
            delta = int(argv[2])

            if raw_target in {"b1", "1"}:
                await dim_adjust("B1", delta)
                return
            if raw_target in {"b2", "2"}:
                await dim_adjust("B2", delta)
                return

            # still allow the old explicit form
            target = argv[1].upper()
            await dim_adjust(target, delta)
            return

        raise SystemExit("Usage: lights dim <delta> | lights dim <B1|B2> <delta>")

    # ---------------- fade ----------------
    if cmd == "fade":
        if len(argv) != 3:
            raise SystemExit("Usage: lights fade <preset> <seconds>")
        stop_running_effect(active_group())
        await fade_to(argv[1], float(argv[2]))
        save_last_mode(argv[1], active_group())
        return

    # ---------------- bulb targeting ----------------
    if cmd == "b1":
        if len(argv) != 2:
            raise SystemExit("Usage: lights b1 <preset>")
        stop_running_effect(active_group())
        await turn_on_b1(argv[1])
        save_last_mode(argv[1], active_group())
        return

    if cmd == "b2":
        if len(argv) != 2:
            raise SystemExit("Usage: lights b2 <preset>")
        stop_running_effect(active_group())
        await turn_on_b2(argv[1])
        save_last_mode(argv[1], active_group())
        return

    if cmd == "duo":
        if len(argv) != 3:
            raise SystemExit("Usage: lights duo <preset1> <preset2>")
        stop_running_effect(active_group())
        await turn_duo(argv[1], argv[2])
        save_last_mode(argv[1], active_group())
        return

    # ---------------- background effects ----------------
    if cmd in {
        "fireplace_ambient",
        "asym_static",
        "cozy_ambient",
        "candle_pair",
        "breathe_soft",
        "focus_wave",
        "dusk_drift",
        "embers",
        "bonfire",
        "aurora",
        "police_siren",
        "hearth",
        "abyss",
        "storm_distant",
    }:
        launch_background(cmd, group_for_bg)
        save_last_mode(cmd, active_group())
        return

    # ---------------- static presets ----------------
    if cmd not in PRESETS:
        print(f"ERROR        Unknown command or preset: {cmd}")
        print("")
        print_help()
        raise SystemExit(2)

    stop_running_effect(active_group())
    await turn_on(cmd)
    save_last_mode(cmd, active_group())


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
