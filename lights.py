#!/home/pi/venvs/wiz/bin/python

import asyncio
import colorsys
import fcntl
import json
import math
import os
import random
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pywizlight import PilotBuilder, wizlight
from pywizlight.exceptions import WizLightConnectionError

from lights_config import GROUP_ALIASES, GROUPS, IPS, ROOM_BY_IP

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

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


def _normalize_cmd(cmd: str) -> str:
    normalized = cmd.strip().lower()
    if normalized in {"-h", "--help"}:
        return normalized
    return normalized.replace("-", "_")


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
PENDING_MODE_FILE = STATE_DIR / "pending_mode"
AUDIT_LOG_FILE = STATE_DIR / "audit.log"
EFFECT_LOG_FILE = STATE_DIR / "effects.log"
PENDING_MODE_TTL_SEC = 8.0
COOK_OVERRIDE_FILE = STATE_DIR / "cook_override.json"
COOK_DEBOUNCE_FILE = STATE_DIR / "cook_last_press"
COOK_LOCK_FILE = STATE_DIR / "cook.lock"
COOK_LOG_FILE = STATE_DIR / "cook.log"
COOK_MODE = "cook_dim"
DRY_RUN = os.getenv("LIGHTS_DRY_RUN", "").lower() in {"1", "true", "yes", "on"}
MINOTAUR_SOURCE = "wiz_lights"
MINOTAUR_CORE_DIR = Path(os.environ.get("MINOTAUR_CORE_DIR", "/mnt/ssd/home-pi/projects/minotaur_core"))
MINOTAUR_CLI = MINOTAUR_CORE_DIR / ".venv" / "bin" / "minotaur-core"
MINOTAUR_HTTP_HELPER = MINOTAUR_CORE_DIR / "scripts" / "emit_event.py"
MINOTAUR_ALLOWED_EVENTS = {
    "lights.scene.changed",
    "lights.effect.started",
    "lights.effect.stopped",
    "lights.off",
    "lights.error",
}

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
    "breathe",
    "breathe_soft",
    "sleep_breathe",
    "focus_wave",
    "dusk_drift",
    "hearth",
    "abyss",
    "smolder",
    "midnight_embers",
    "blue_coals",
    "afterglow",
    "campfire_low",
    "lava_lamp",
    "psychedelic",
    "sleep_flow",
    "storm_distant",
    "police_siren",
    "neon_rain",
    "deep_space",
    "moonlight",
    "synthwave",
    "biohazard",
    "underwater_ruins",
    "arc_reactor",
}

COMMAND_ALIASES = {
    "minotaur_breath": "breathe",
}

VISIBLE_MENU_ITEMS = [
    "cook",
    "warm",
    "soft",
    "bright",
    "cool",
    "night",
    "movie",
    "tiffany",
    "golden_white",
    "daylight",
    "fireplace",
    "candlelight",
    "focus",
    "relax",
    "embers",
    "hearth",
    "breathe",
    "aurora",
    "storm_distant",
    "moonlight",
    "deep_space",
    "lava_lamp",
    "police_siren",
    "alert_pulse",
]

CUSTOM_SCENES = {
    "bedtime_flow": {
        "result_mode": "off",
        "intro": 4.0,
        "interval": 0.25,
        "steps": [
            {"r": 0, "g": 0, "b": 0, "c": 0, "w": 255, "brightness": 192, "hold": 20, "fade": 10},
            {"r": 255, "g": 50, "b": 0, "c": 0, "w": 127, "brightness": 192, "hold": 0, "fade": 15},
            {"r": 255, "g": 25, "b": 0, "c": 0, "w": 0, "brightness": 192, "hold": 0, "fade": 15},
            {"r": 255, "g": 0, "b": 0, "c": 0, "w": 0, "brightness": 192, "hold": 10, "fade": 30},
            {"r": 0, "g": 0, "b": 0, "c": 0, "w": 0, "brightness": 0, "hold": 0, "fade": 0},
        ],
    },
    "sunrise_flow": {
        "result_mode": "bright",
        "intro": 8.0,
        "interval": 0.25,
        "steps": [
            {"r": 255, "g": 0, "b": 0, "c": 20, "w": 0, "brightness": 10, "hold": 15, "fade": 10},
            {"r": 255, "g": 0, "b": 0, "c": 20, "w": 0, "brightness": 200, "hold": 10, "fade": 10},
            {"r": 255, "g": 0, "b": 100, "c": 20, "w": 0, "brightness": 192, "hold": 5, "fade": 10},
            {"r": 255, "g": 0, "b": 200, "c": 127, "w": 0, "brightness": 192, "hold": 0, "fade": 10},
            {"r": 100, "g": 100, "b": 255, "c": 255, "w": 255, "brightness": 255, "hold": 0, "fade": 0},
        ],
    },
    "golden_hour_loop": {
        "intro": 5.0,
        "interval": 0.35,
        "loop": True,
        "color_space": "hsv",
        "per_bulb_offset": 0.8,
        "jitter": {"r": 5, "g": 4, "b": 5, "c": 6, "w": 8, "brightness": 4},
        "steps": [
            {"r": 255, "g": 142, "b": 34, "c": 0, "w": 90, "brightness": 95, "hold": [8, 16], "fade": [16, 28]},
            {"r": 255, "g": 84, "b": 78, "c": 0, "w": 50, "brightness": 82, "hold": [6, 12], "fade": [18, 30]},
            {"r": 255, "g": 188, "b": 112, "c": 12, "w": 170, "brightness": 105, "hold": [8, 18], "fade": [18, 32]},
            {"r": 255, "g": 116, "b": 48, "c": 0, "w": 125, "brightness": 88, "hold": [8, 14], "fade": [16, 28]},
        ],
    },
    "storm": {
        "intro": 3.0,
        "interval": 0.18,
        "loop": True,
        "jitter": {"r": 3, "g": 4, "b": 14, "c": 8, "brightness": 8},
        "steps": [
            {"r": 5, "g": 12, "b": 56, "c": 22, "w": 0, "brightness": 30, "hold": [7, 18], "fade": [2.0, 4.5]},
            {"r": 210, "g": 230, "b": 255, "c": 255, "w": 40, "brightness": 255, "hold": [0.04, 0.12], "fade": [0.04, 0.12], "chance": 0.55, "target": "random"},
            {"r": 12, "g": 20, "b": 70, "c": 45, "w": 0, "brightness": 42, "hold": [0.08, 0.35], "fade": [0.08, 0.22], "target": "random"},
            {"r": 245, "g": 250, "b": 255, "c": 255, "w": 65, "brightness": 255, "hold": [0.03, 0.08], "fade": [0.02, 0.08], "chance": 0.32, "target": "random"},
            {"r": 4, "g": 10, "b": 48, "c": 18, "w": 0, "brightness": 26, "hold": [5, 14], "fade": [0.4, 1.4]},
        ],
    },
    "sparkle_warm": {
        "intro": 4.0,
        "interval": 0.2,
        "loop": True,
        "jitter": {"r": 8, "g": 6, "b": 4, "w": 10, "brightness": 7},
        "steps": [
            {"r": 255, "g": 126, "b": 38, "c": 0, "w": 135, "brightness": 56, "hold": [3, 8], "fade": [1.8, 4.0]},
            {"r": 255, "g": 198, "b": 96, "c": 0, "w": 210, "brightness": 116, "hold": [0.08, 0.22], "fade": [0.08, 0.2], "chance": 0.72, "target": "random"},
            {"r": 255, "g": 116, "b": 30, "c": 0, "w": 120, "brightness": 48, "hold": [1.5, 5.5], "fade": [0.4, 1.3]},
            {"r": 255, "g": 176, "b": 68, "c": 0, "w": 185, "brightness": 86, "hold": [0.05, 0.18], "fade": [0.05, 0.18], "chance": 0.45, "target": "random"},
            {"r": 255, "g": 104, "b": 26, "c": 0, "w": 110, "brightness": 50, "hold": [2.5, 7.0], "fade": [0.8, 2.2]},
        ],
    },
    "meow_wolf": {
        "intro": 4.0,
        "interval": 0.2,
        "loop": True,
        "color_space": "hsv",
        "per_bulb_offset": 0.35,
        "jitter": {"r": 8, "g": 7, "b": 8, "c": 6, "w": 8, "brightness": 6},
        "steps": [
            {"r": 255, "g": 110, "b": 210, "c": 18, "w": 20, "brightness": 105, "hold": [4, 8], "fade": [10, 16]},
            {"r": 140, "g": 255, "b": 235, "c": 48, "w": 40, "brightness": 125, "hold": [0.08, 0.18], "fade": [0.15, 0.45], "chance": 0.85, "target": "random"},
            {"r": 255, "g": 220, "b": 120, "c": 24, "w": 150, "brightness": 112, "hold": [2, 5], "fade": [8, 14]},
            {"r": 170, "g": 120, "b": 255, "c": 14, "w": 30, "brightness": 100, "hold": [3, 7], "fade": [10, 18]},
            {"r": 255, "g": 160, "b": 80, "c": 8, "w": 60, "brightness": 110, "hold": [2, 6], "fade": [8, 14]},
        ],
    },
}


def _event_room_name(group: str | None = None) -> str:
    g = active_group() if group is None else group
    return g or "all"


def _safe_event_metadata(**metadata) -> dict[str, str]:
    allowed = {"scene_name", "room_name", "target_name", "command", "mode"}
    safe: dict[str, str] = {}
    for key, value in metadata.items():
        if key not in allowed or value is None:
            continue
        text = str(value).strip()
        if text:
            safe[key] = text[:120]
    return safe


def emit_minotaur_event(
    event_type: str,
    title: str,
    message: str = "",
    *,
    metadata: dict[str, str] | None = None,
    severity: str = "info",
) -> None:
    """Best-effort Minotaur emission that must never block light commands."""
    if event_type not in MINOTAUR_ALLOWED_EVENTS:
        return
    metadata_json = json.dumps(metadata or {}, sort_keys=True)
    emitters = [
        ("cli", MINOTAUR_CLI),
        ("http", MINOTAUR_HTTP_HELPER),
    ]
    for kind, helper in emitters:
        if not helper.exists():
            continue
        if kind == "cli":
            command = [
                str(helper),
                "emit",
                "--source",
                MINOTAUR_SOURCE,
                "--event-type",
                event_type,
                "--title",
                title,
                "--message",
                message,
                "--severity",
                severity,
                "--tags",
                "lights,wiz",
                "--metadata",
                metadata_json,
            ]
        else:
            command = [
                str(helper),
                "--source",
                MINOTAUR_SOURCE,
                "--event-type",
                event_type,
                "--title",
                title,
                "--message",
                message,
                "--severity",
                severity,
                "--tags",
                "lights,wiz",
                "--metadata",
                metadata_json,
            ]
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return
        return


def emit_scene_changed(scene_name: str, *, command: str | None = None, target_name: str | None = None) -> None:
    room = _event_room_name()
    emit_minotaur_event(
        "lights.scene.changed",
        "WiZ scene changed",
        f"Set {scene_name} for {room}.",
        metadata=_safe_event_metadata(
            scene_name=scene_name,
            room_name=room,
            target_name=target_name,
            command=command,
            mode=scene_name,
        ),
    )


def emit_effect_started(effect_name: str, *, command: str | None = None) -> None:
    room = _event_room_name()
    emit_minotaur_event(
        "lights.effect.started",
        "WiZ effect started",
        f"Started {effect_name} for {room}.",
        metadata=_safe_event_metadata(
            scene_name=effect_name,
            room_name=room,
            command=command,
            mode=effect_name,
        ),
    )


def emit_effect_stopped(effect_name: str | None = None, *, command: str = "stop") -> None:
    room = _event_room_name()
    emit_minotaur_event(
        "lights.effect.stopped",
        "WiZ effect stopped",
        f"Stopped light effects for {room}.",
        metadata=_safe_event_metadata(
            scene_name=effect_name,
            room_name=room,
            command=command,
            mode=effect_name,
        ),
    )


def emit_lights_off(*, command: str = "off") -> None:
    room = _event_room_name()
    emit_minotaur_event(
        "lights.off",
        "WiZ lights off",
        f"Turned lights off for {room}.",
        metadata=_safe_event_metadata(room_name=room, command=command, mode="off"),
    )


def emit_lights_error(command: str, error: str) -> None:
    emit_minotaur_event(
        "lights.error",
        "WiZ lights error",
        error[:200],
        metadata=_safe_event_metadata(
            room_name=_event_room_name(),
            command=command,
            mode=command,
        ),
        severity="error",
    )

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
    "tiffany": {
        "brightness": 95,
        "pilot": PilotBuilder(
            brightness=95,
            rgb=(180, 145, 95)
        )
    },
    "cook_dim": {"brightness": 111, "pilot": PilotBuilder(brightness=111, colortemp=2700)},
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
    "hearth": (255, 190, 110),
    "fireplace_ambient": (255, 125, 45),
    "breathe": (255, 130, 55),
    "sleep_breathe": (180, 75, 35),
    "storm_distant": (150, 165, 190),
    "cozy_ambient": (255, 175, 95),
    "candle_pair": (255, 170, 80),
    "asym_static": (255, 215, 170),
    "breathe_soft": (255, 145, 90),
    "focus_wave": (210, 235, 255),
    "dusk_drift": (255, 140, 85),
    "police_siren": (255, 0, 0),
    "abyss": (60, 0, 150),
    "smolder": (120, 20, 28),
    "midnight_embers": (70, 0, 95),
    "blue_coals": (10, 35, 110),
    "afterglow": (95, 35, 50),
    "campfire_low": (160, 70, 28),
    "lava_lamp": (95, 0, 120),
    "psychedelic": (255, 0, 255),
    "sleep_flow": (35, 10, 95),
    "neon_rain": (0, 220, 255),
    "deep_space": (45, 20, 120),
    "moonlight": (165, 205, 255),
    "synthwave": (255, 0, 180),
    "biohazard": (90, 255, 30),
    "underwater_ruins": (0, 125, 150),
    "arc_reactor": (80, 235, 255),
    "meow_wolf": (255, 170, 220),
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


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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

    _safe_unlink(ef)
    return False


def set_effect_running(name: str, group: str | None = None) -> None:
    g = active_group() if group is None else group
    ef = _effect_file(g)
    bf = _effect_bri_file(g)

    try:
        ef.write_text(f"{name}\n{os.getpid()}\n")
    except OSError:
        pass
    try:
        if not bf.exists():
            bf.write_text("255")
    except OSError:
        pass


def clear_effect_running(group: str | None = None) -> None:
    g = active_group() if group is None else group
    _safe_unlink(_effect_file(g))


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
    try:
        bf.write_text(str(v))
    except OSError:
        pass
    return v


def effect_scale(group: str | None = None) -> float:
    return load_effect_bri(255, group=group) / 255.0


def scale_bri(b: float, group: str | None = None) -> int:
    s = effect_scale(group=group)
    return max(1, min(255, int(round(float(b) * s))))


def scale_scene_bri(b: float, group: str | None = None) -> int:
    raw = max(0, min(255, int(round(float(b)))))
    if raw <= 0:
        return 0
    effect_group = effect_group_affecting_target(group)
    if effect_group is None:
        return raw
    s = effect_scale(group=effect_group)
    return max(1, min(255, int(round(raw * s))))


def dim_running_effect(delta: int, group: str | None = None) -> bool:
    effect_group = effect_group_affecting_target(group)
    if effect_group is None:
        return False

    cur = load_effect_bri(255, group=effect_group)
    new = save_effect_bri(cur + int(delta), group=effect_group)
    print(f"EFFECT_DIM    group={effect_group or 'all'} bri={new}")
    return True


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
                # Give background effects a brief window to exit cleanly so a
                # replacement preset/scene does not get immediately overwritten.
                deadline = time.monotonic() + 0.6
                while time.monotonic() < deadline:
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.03)
                if _pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
        except Exception:
            pass
        _safe_unlink(ef)


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


def load_effect_affecting_target(group: str | None = None) -> str | None:
    g = active_group() if group is None else group
    running = load_running_effect_name(g)
    if running:
        return running
    if g and g != "all":
        return load_running_effect_name("all")
    return None


def effect_group_affecting_target(group: str | None = None) -> str | None:
    g = active_group() if group is None else group
    if effect_is_running(g):
        return g or "all"
    if g and g != "all" and effect_is_running("all"):
        return "all"
    return None


def stop_effects_affecting_target(group: str | None = None) -> None:
    g = active_group() if group is None else group
    if g and g != "all":
        stop_running_effect("all")
        stop_running_effect(g)
        return
    stop_running_effect(None)

# --------------------------------------------------
# STATE HELPERS
# --------------------------------------------------

def _last_mode_file(group: str | None) -> Path:
    if group:
        return STATE_DIR / f"last_mode_{group}"
    return STATE_DIR / "last_mode"


def save_last_mode(mode: str, group: str | None = None) -> None:
    path = _last_mode_file(group)
    try:
        path.write_text(mode)
    except OSError:
        pass


def load_last_mode(group: str | None = None) -> str | None:
    path = _last_mode_file(group)
    if path.exists():
        return path.read_text().strip()
    return None


def save_pending_mode(mode: str, group: str | None = None) -> None:
    g = group if group is not None else active_group()
    try:
        PENDING_MODE_FILE.write_text(f"{mode}\n{time.time()}\n{g or 'all'}\n")
    except Exception:
        pass


def clear_pending_mode() -> None:
    try:
        PENDING_MODE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def load_pending_mode() -> str | None:
    try:
        lines = PENDING_MODE_FILE.read_text().splitlines()
    except Exception:
        return None

    if len(lines) < 2:
        PENDING_MODE_FILE.unlink(missing_ok=True)
        return None

    mode = lines[0].strip()
    try:
        stamp = float(lines[1].strip())
    except Exception:
        PENDING_MODE_FILE.unlink(missing_ok=True)
        return None

    if not mode or time.time() - stamp > PENDING_MODE_TTL_SEC:
        PENDING_MODE_FILE.unlink(missing_ok=True)
        return None

    return mode


def load_last_mode_for_target(group: str | None = None) -> str | None:
    g = active_group() if group is None else group
    mode = load_last_mode(g)
    if mode:
        return mode
    if g and g != "all":
        return load_last_mode(None)
    return None


def target_power_state(target_ips: list[str] | None = None, timeout: float = 0.18) -> bool | None:
    """
    Return True if any reachable bulb is on, False if reachable bulbs are all off,
    or None if no bulbs responded.
    """
    ips = target_ips if target_ips is not None else _target_ips()
    saw_response = False

    for ip in ips:
        result = get_pilot_raw(ip, timeout)
        if result is None:
            continue

        saw_response = True
        state = result.get("result", {}).get("state")
        if state:
            return True

    if saw_response:
        return False
    return None


def current_mode_names() -> list[str]:
    pending = load_pending_mode()
    if pending == "off":
        return [pending]
    if pending == "cook" and not COOK_OVERRIDE_FILE.exists():
        pending = None
    if COOK_OVERRIDE_FILE.exists():
        return ["cook"]

    def _read_running_name(group: str | None) -> str | None:
        path = _effect_file(group)
        try:
            lines = path.read_text().splitlines()
        except Exception:
            return None

        name = lines[0].strip() if lines else ""
        if not name:
            return None

        try:
            pid = int(lines[1]) if len(lines) > 1 else None
        except Exception:
            pid = None

        if pid and not _pid_alive(pid):
            return None
        return name

    names: list[str] = []
    groups: list[str | None] = [None]
    groups.extend(g for g in GROUPS.keys() if g != "all")

    for group in groups:
        name = _read_running_name(group)
        if name and name not in names:
            names.append(name)

    if names:
        return names

    try:
        power_state = target_power_state()
    except OSError:
        power_state = None

    if power_state is False:
        return ["off"]
    if pending:
        return [pending]
    if power_state is None:
        return ["unknown"]

    for group in groups:
        name = load_last_mode(group)
        if name and name not in BACKGROUND_EFFECTS and name not in CUSTOM_SCENES:
            return [name]
    return ["on"]

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


def _dimming_percent_to_brightness(dimming: int | None) -> int:
    if dimming is None:
        return 0
    pct = max(0, min(100, int(dimming)))
    return max(0, min(255, int(round((pct / 100) * 255))))


def _clamp_channel(value: int | float | None) -> int:
    if value is None:
        return 0
    return max(0, min(255, int(round(float(value)))))


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


def send_raw_pilot(
    ip: str,
    r: int = 0,
    g: int = 0,
    b: int = 0,
    c: int = 0,
    w: int = 0,
    brightness_0_255: int = 140,
) -> None:
    brightness = _clamp_channel(brightness_0_255)
    if brightness <= 0:
        send_raw_off(ip)
        return

    dimming = _brightness_to_dimming_percent(brightness)
    payload = {
        "id": 1,
        "method": "setPilot",
        "params": {
            "state": True,
            "r": _clamp_channel(r),
            "g": _clamp_channel(g),
            "b": _clamp_channel(b),
            "c": _clamp_channel(c),
            "w": _clamp_channel(w),
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
    save_pending_mode(cmd, group)
    running = load_running_effect_name(group)
    if running == cmd:
        if group:
            print(f"EFFECT {cmd} already running ({group}), restarting")
        else:
            print(f"EFFECT {cmd} already running, restarting")
        stop_effects_affecting_target(group)

    else:
        stop_effects_affecting_target(group)

    args = [sys.executable, __file__, "--bg", cmd]
    if group:
        args.append(group)

    try:
        log = EFFECT_LOG_FILE.open("a", encoding="utf-8")
    except Exception:
        log = subprocess.DEVNULL

    try:
        subprocess.Popen(
            args,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        if hasattr(log, "close"):
            log.close()

    if group:
        print(f"EFFECT {cmd} started ({group})")
    else:
        print(f"EFFECT {cmd} started")


def launch_custom_scene(scene_name: str, group: str | None) -> None:
    if _scene_uses_background(scene_name):
        launch_background(scene_name, group)
        return

    raise ValueError(f"Custom scene is not a background scene: {scene_name}")


async def run_or_launch_custom_scene(scene_name: str, group_for_bg: str | None = None) -> None:
    if _scene_uses_background(scene_name):
        launch_custom_scene(scene_name, group_for_bg)
        return
    await run_custom_scene(scene_name)

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

        running = load_effect_affecting_target()
        last = load_last_mode_for_target()

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

            last = load_last_mode_for_target()
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

SCENE_CHANNELS = ("r", "g", "b", "c", "w", "brightness")


def _scene_step_value(step: dict, key: str, default: int = 0) -> int:
    aliases = {
        "c": ("c", "cw", "cold_white"),
        "w": ("w", "ww", "warm_white"),
        "brightness": ("brightness", "bri"),
    }
    for candidate in aliases.get(key, (key,)):
        if candidate in step:
            return _clamp_channel(step.get(candidate))
    return default


def _normalize_scene_step(step: dict) -> dict[str, int]:
    return {key: _scene_step_value(step, key) for key in SCENE_CHANNELS}


def _scene_number(value, default: float = 0.0) -> float:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        lo = float(value[0])
        hi = float(value[1])
        return random.uniform(min(lo, hi), max(lo, hi))
    if value is None:
        return float(default)
    return float(value)


def _scene_uses_background(scene_name: str) -> bool:
    scene = CUSTOM_SCENES.get(scene_name, {})
    return bool(scene.get("loop"))


def _raw_state_to_scene_step(raw: dict | None) -> dict[str, int]:
    result = raw.get("result", {}) if isinstance(raw, dict) else {}
    on = bool(result.get("state", False))
    brightness = _dimming_percent_to_brightness(result.get("dimming")) if on else 0
    return {
        "r": _clamp_channel(result.get("r")),
        "g": _clamp_channel(result.get("g")),
        "b": _clamp_channel(result.get("b")),
        "c": _clamp_channel(result.get("c")),
        "w": _clamp_channel(result.get("w")),
        "brightness": brightness,
    }


def _lerp_rgb_hsv(start: dict[str, int], end: dict[str, int], amount: float) -> tuple[int, int, int]:
    sr, sg, sb = start["r"] / 255.0, start["g"] / 255.0, start["b"] / 255.0
    er, eg, eb = end["r"] / 255.0, end["g"] / 255.0, end["b"] / 255.0
    sh, ss, sv = colorsys.rgb_to_hsv(sr, sg, sb)
    eh, es, ev = colorsys.rgb_to_hsv(er, eg, eb)

    hue_delta = eh - sh
    if hue_delta > 0.5:
        hue_delta -= 1.0
    elif hue_delta < -0.5:
        hue_delta += 1.0

    h = (sh + hue_delta * amount) % 1.0
    s = ss + (es - ss) * amount
    v = sv + (ev - sv) * amount
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (_clamp_channel(r * 255), _clamp_channel(g * 255), _clamp_channel(b * 255))


def _lerp_scene_step(start: dict[str, int], end: dict[str, int], amount: float, color_space: str = "rgb") -> dict[str, int]:
    step = {
        key: _clamp_channel(start[key] + (end[key] - start[key]) * amount)
        for key in SCENE_CHANNELS
    }
    if color_space == "hsv":
        step["r"], step["g"], step["b"] = _lerp_rgb_hsv(start, end, amount)
    return step


def _scene_jitter_value(jitter, key: str) -> int:
    if isinstance(jitter, dict):
        return int(jitter.get(key, 0))
    if jitter is None:
        return 0
    return int(jitter)


def _jitter_scene_step(step: dict[str, int], jitter=None) -> dict[str, int]:
    if not jitter:
        return dict(step)
    result = {}
    for key, value in step.items():
        spread = _scene_jitter_value(jitter, key)
        result[key] = _clamp_channel(value + random.randint(-spread, spread)) if spread > 0 else value
    return result


async def _scene_sleep(seconds: float, loop_scene: bool) -> None:
    end_time = asyncio.get_event_loop().time() + max(0.0, float(seconds))
    while True:
        remaining = end_time - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        if loop_scene and effect_should_stop():
            return
        await asyncio.sleep(min(remaining, 0.5))


async def _send_scene_step_to_bulbs(bulbs, step: dict[str, int], jitter=None) -> None:
    def _prepare_step() -> dict[str, int]:
        send_step = _jitter_scene_step(step, jitter)
        send_step["brightness"] = scale_scene_bri(send_step["brightness"])
        return send_step

    await asyncio.gather(*[
        asyncio.to_thread(
            send_raw_pilot,
            bulb.ip,
            bulb_step["r"],
            bulb_step["g"],
            bulb_step["b"],
            bulb_step["c"],
            bulb_step["w"],
            bulb_step["brightness"],
        )
        for bulb in bulbs
        for bulb_step in [_prepare_step()]
    ])


async def _fade_scene_step(
    bulbs,
    start: dict[str, int],
    end: dict[str, int],
    seconds: float,
    interval: float,
    color_space: str = "rgb",
    jitter=None,
) -> None:
    if seconds <= 0:
        await _send_scene_step_to_bulbs(bulbs, end, jitter=jitter)
        return

    steps = max(1, int(float(seconds) / float(interval)))
    delay = float(seconds) / steps
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    for i in range(steps):
        amount = (i + 1) / steps
        step = _lerp_scene_step(start, end, amount, color_space=color_space)
        await _send_scene_step_to_bulbs(bulbs, step, jitter=jitter)
        next_tick = start_time + (i + 1) * delay
        await asyncio.sleep(max(0, next_tick - loop.time()))


def _scene_result_mode(scene_name: str) -> str:
    scene = CUSTOM_SCENES.get(scene_name, {})
    result_mode = scene.get("result_mode")
    if isinstance(result_mode, str) and result_mode.strip():
        return _normalize_cmd(result_mode)
    return scene_name


async def _scene_sleep_keep_mode(seconds: float, loop_scene: bool, mode_name: str) -> None:
    end_time = asyncio.get_event_loop().time() + max(0.0, float(seconds))
    while True:
        remaining = end_time - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        save_pending_mode(mode_name, active_group())
        if loop_scene and effect_should_stop():
            return
        await asyncio.sleep(min(remaining, 0.5))


async def _fade_scene_step_keep_mode(
    bulbs,
    start: dict[str, int],
    end: dict[str, int],
    seconds: float,
    interval: float,
    mode_name: str,
    color_space: str = "rgb",
    jitter=None,
) -> None:
    if seconds <= 0:
        save_pending_mode(mode_name, active_group())
        await _send_scene_step_to_bulbs(bulbs, end, jitter=jitter)
        return

    steps = max(1, int(float(seconds) / float(interval)))
    delay = float(seconds) / steps
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    for i in range(steps):
        save_pending_mode(mode_name, active_group())
        amount = (i + 1) / steps
        step = _lerp_scene_step(start, end, amount, color_space=color_space)
        await _send_scene_step_to_bulbs(bulbs, step, jitter=jitter)
        next_tick = start_time + (i + 1) * delay
        await asyncio.sleep(max(0, next_tick - loop.time()))


async def run_custom_scene(name: str) -> None:
    scene_name = _normalize_cmd(name)
    if scene_name not in CUSTOM_SCENES:
        raise ValueError(f"Unknown custom scene: {name}")

    scene = CUSTOM_SCENES[scene_name]
    raw_steps = scene.get("steps", [])
    steps = [_normalize_scene_step(step) for step in raw_steps]
    if not steps:
        raise ValueError(f"Custom scene has no steps: {name}")

    interval = float(scene.get("interval", 0.25))
    color_space = str(scene.get("color_space", "rgb")).lower()
    if color_space not in {"rgb", "hsv"}:
        color_space = "rgb"
    jitter = scene.get("jitter")
    loop_scene = bool(scene.get("loop"))
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running(scene_name)
    if loop_scene:
        print(f"SCENE        {scene_name} background start")

    try:
        save_pending_mode(scene_name, active_group())
        raw_states = await asyncio.gather(*[
            asyncio.to_thread(get_pilot_raw, bulb.ip, 0.6)
            for bulb in bulbs
        ])
        current_steps = {
            bulb.ip: _raw_state_to_scene_step(raw)
            for bulb, raw in zip(bulbs, raw_states)
        }
        first_step = steps[0]
        intro = float(scene.get("intro", 0))

        if intro > 0:
            await asyncio.gather(*[
                _fade_scene_step_keep_mode(
                    [bulb],
                    current_steps[bulb.ip],
                    first_step,
                    intro + (i * float(scene.get("per_bulb_offset", 0))),
                    interval,
                    scene_name,
                    color_space=color_space,
                    jitter=jitter,
                )
                for i, bulb in enumerate(bulbs)
            ])
        else:
            save_pending_mode(scene_name, active_group())
            await _send_scene_step_to_bulbs(bulbs, first_step, jitter=jitter)

        for bulb in bulbs:
            current_steps[bulb.ip] = dict(first_step)

        first_hold = _scene_number(raw_steps[0].get("hold", 0))
        if first_hold > 0:
            await _scene_sleep_keep_mode(first_hold, loop_scene, scene_name)

        while True:
            for index in range(len(steps) - 1):
                save_pending_mode(scene_name, active_group())
                if loop_scene and effect_should_stop():
                    return

                raw_step = raw_steps[index]
                next_raw_step = raw_steps[index + 1]
                if random.random() > float(next_raw_step.get("chance", 1.0)):
                    continue

                target_mode = str(next_raw_step.get("target", "all")).lower()
                if target_mode in {"random", "one"} and len(bulbs) > 1:
                    target_bulbs = [random.choice(bulbs)]
                else:
                    target_bulbs = bulbs

                fade_seconds = _scene_number(raw_step.get("fade", 0))
                await asyncio.gather(*[
                    _fade_scene_step_keep_mode(
                        [bulb],
                        current_steps[bulb.ip],
                        steps[index + 1],
                        fade_seconds,
                        interval,
                        scene_name,
                        color_space=color_space,
                        jitter=jitter,
                    )
                    for bulb in target_bulbs
                ])

                for bulb in target_bulbs:
                    current_steps[bulb.ip] = dict(steps[index + 1])

                hold_seconds = _scene_number(next_raw_step.get("hold", 0))
                if hold_seconds > 0:
                    await _scene_sleep_keep_mode(hold_seconds, loop_scene, scene_name)

            if not loop_scene:
                break

            if effect_should_stop():
                return

            fade_seconds = _scene_number(raw_steps[-1].get("fade", 0))
            await asyncio.gather(*[
                _fade_scene_step_keep_mode(
                    [bulb],
                    current_steps[bulb.ip],
                    first_step,
                    fade_seconds,
                    interval,
                    scene_name,
                    color_space=color_space,
                    jitter=jitter,
                )
                for bulb in bulbs
            ])
            for bulb in bulbs:
                current_steps[bulb.ip] = dict(first_step)

            first_hold = _scene_number(raw_steps[0].get("hold", 0))
            if first_hold > 0:
                await _scene_sleep_keep_mode(first_hold, loop_scene, scene_name)

        for bulb in bulbs:
            print(f"SCENE        {bulb.ip} -> {scene_name}")

    finally:
        clear_effect_running()
        await close_all(bulbs)


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

        while (asyncio.get_event_loop().time() < end_time) and (not effect_should_stop()):
            bri = load_effect_bri(255)
            red = PilotBuilder(brightness=bri, rgb=(255, 0, 0))
            blue = PilotBuilder(brightness=bri, rgb=(0, 120, 255))

            await asyncio.gather(*[
                bulb.turn_on(red if i % 2 == 0 else blue)
                for i, bulb in enumerate(bulbs)
            ])
            await asyncio.sleep(float(interval))

            await asyncio.gather(*[
                bulb.turn_on(blue if i % 2 == 0 else red)
                for i, bulb in enumerate(bulbs)
            ])
            await asyncio.sleep(float(interval))

        bri = load_effect_bri(255)
        white = PilotBuilder(brightness=bri, colortemp=6500)
        await asyncio.gather(*[bulb.turn_on(white) for bulb in bulbs])
        print("POLICE_SIREN  " + " ".join(bulb.ip for bulb in bulbs))

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
            _fireplace_organic_single(b1, scene_id, base_bri=118, bri_jitter=12, min_wait=8.0, max_wait=24.0),
            _fireplace_async_single(b2, scene_id, base_bri=78, bri_jitter=10, min_wait=0.8, max_wait=2.4),
        )
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def embers():
    await fireplace_organic(managed=True, effect_name="embers")


async def bonfire():
    await bonfire_organic(
        min_wait=0.7,
        max_wait=2.8,
        base_bri=175,
        bri_jitter=42,
        managed=True,
        effect_name="bonfire",
    )


async def bonfire_organic(min_wait=2, max_wait=9, base_bri=145, bri_jitter=28, managed=True, effect_name="bonfire_organic"):
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("bonfire_organic requires 2 bulbs")

    scene_id = 5
    if managed:
        set_effect_running(effect_name)
    print("BONFIRE_ORG   background start")

    try:
        for b in bulbs:
            send_raw_scene(b.ip, scene_id, int(base_bri))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.randrange(len(bulbs))
            send_raw_scene(bulbs[idx].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
            print(f"BONFIRE_ORG   reseed {bulbs[idx].ip}")

            if len(bulbs) > 1 and random.random() < 0.72:
                other = random.choice([i for i in range(len(bulbs)) if i != idx])
                delay = random.uniform(0.03, 0.3)
                await asyncio.sleep(delay)
                send_raw_scene(bulbs[other].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
                print(f"BONFIRE_ORG   reseed {bulbs[other].ip} after {delay:.2f}s")

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
        await close_all(bulbs)


async def fireplace_organic(min_wait=6, max_wait=22, base_bri=120, bri_jitter=18, managed=True, effect_name="fireplace_organic"):
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("fireplace_organic requires 2 bulbs")

    scene_id = 5
    if managed:
        set_effect_running(effect_name)
    print("FIREPLACE_ORG  background start")

    try:
        for b in bulbs:
            send_raw_scene(b.ip, scene_id, int(base_bri))
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.randrange(len(bulbs))
            send_raw_scene(bulbs[idx].ip, scene_id, _fireplace_rand_bri(base_bri, bri_jitter))
            print(f"FIREPLACE_ORG  reseed {bulbs[idx].ip}")

            if len(bulbs) > 1 and random.random() < 0.35:
                other = random.choice([i for i in range(len(bulbs)) if i != idx])
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
    if len(bulbs) < 2:
        raise RuntimeError("fireplace_ambient requires at least 2 bulbs")

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
            idx = random.randrange(len(bulbs))
            other = random.choice([i for i in range(len(bulbs)) if i != idx])
            src = bulbs[idx]
            amb = bulbs[other]

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


def _cosine_ease(amount: float) -> float:
    amount = max(0.0, min(1.0, float(amount)))
    return 0.5 - 0.5 * math.cos(math.pi * amount)


@dataclass
class BreathCycle:
    inhale: float
    top_pause: float
    exhale: float
    bottom_pause: float
    low_bri: int
    high_bri: int
    low_rgb: tuple[int, int, int]
    high_rgb: tuple[int, int, int]
    peak_rgb: tuple[int, int, int]
    start_time: float
    shimmer: bool
    shimmer_amount: float
    shimmer_phase: float

    @property
    def duration(self) -> float:
        return self.inhale + self.top_pause + self.exhale + self.bottom_pause


def _rand_pct_bri(bounds: tuple[float, float]) -> int:
    pct = random.uniform(float(bounds[0]), float(bounds[1]))
    return max(1, min(255, int(round(255 * (pct / 100.0)))))


def _rand_rgb_channel(bounds: tuple[int, int]) -> int:
    return random.randint(int(bounds[0]), int(bounds[1]))


def _new_breath_cycle(
    start_time: float,
    inhale_range: tuple[float, float],
    top_pause_range: tuple[float, float],
    exhale_range: tuple[float, float],
    bottom_pause_range: tuple[float, float],
    min_pct_range: tuple[float, float],
    max_pct_range: tuple[float, float],
    shimmer_chance: float,
) -> BreathCycle:
    return BreathCycle(
        inhale=random.uniform(*inhale_range),
        top_pause=random.uniform(*top_pause_range),
        exhale=random.uniform(*exhale_range),
        bottom_pause=random.uniform(*bottom_pause_range),
        low_bri=_rand_pct_bri(min_pct_range),
        high_bri=_rand_pct_bri(max_pct_range),
        low_rgb=(
            _rand_rgb_channel((35, 55)),
            _rand_rgb_channel((12, 20)),
            _rand_rgb_channel((4, 8)),
        ),
        high_rgb=(
            _rand_rgb_channel((212, 228)),
            _rand_rgb_channel((88, 104)),
            _rand_rgb_channel((30, 42)),
        ),
        peak_rgb=(
            _rand_rgb_channel((245, 255)),
            _rand_rgb_channel((132, 150)),
            _rand_rgb_channel((60, 76)),
        ),
        start_time=start_time,
        shimmer=random.random() < float(shimmer_chance),
        shimmer_amount=random.uniform(0.01, 0.03),
        shimmer_phase=random.uniform(0.0, math.tau),
    )


def _breath_amount(phase: float, cycle: BreathCycle) -> float:
    if phase < 0:
        return 0.0
    if phase < cycle.inhale:
        return _cosine_ease(phase / cycle.inhale)
    phase -= cycle.inhale
    if phase < cycle.top_pause:
        return 1.0
    phase -= cycle.top_pause
    if phase < cycle.exhale:
        return 1.0 - _cosine_ease(phase / cycle.exhale)
    return 0.0


def _stable_bulb_offset(ip: str) -> float:
    # Stable per bulb, intentionally tiny, so the room breathes without robotic sync.
    total = sum((idx + 1) * ord(ch) for idx, ch in enumerate(ip))
    return 0.1 + ((total % 401) / 1000.0)


def _breath_room_delay(ip: str, kitchen_delay: float) -> float:
    room = ROOM_BY_IP.get(ip, "").lower()
    if room == "kitchen":
        return float(kitchen_delay)
    return 0.0


def _lerp_rgb(low: tuple[int, int, int], high: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return (
        _lerp_int(low[0], high[0], amount),
        _lerp_int(low[1], high[1], amount),
        _lerp_int(low[2], high[2], amount),
    )


def _breath_shimmer(cycle: BreathCycle, phase: float) -> float:
    exhale_start = cycle.inhale + cycle.top_pause
    exhale_phase = phase - exhale_start
    if not cycle.shimmer or exhale_phase < cycle.exhale * 0.75:
        return 0.0

    # Rare, slow ember motion near the bottom of some exhales; never a flicker.
    wobble = math.sin((exhale_phase * 1.6) + cycle.shimmer_phase)
    fade_in = _cosine_ease((exhale_phase - cycle.exhale * 0.75) / (cycle.exhale * 0.25))
    return cycle.shimmer_amount * fade_in * wobble


async def minotaur_breath(
    effect_name: str = "breathe",
    inhale_range: tuple[float, float] = (3.2, 4.2),
    top_pause_range: tuple[float, float] = (0.4, 1.0),
    exhale_range: tuple[float, float] = (5.5, 7.5),
    bottom_pause_range: tuple[float, float] = (1.2, 2.8),
    min_pct_range: tuple[float, float] = (2.0, 4.0),
    max_pct_range: tuple[float, float] = (32.0, 45.0),
    shimmer_chance: float = 0.28,
    updates_per_second: float = 4.0,
) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    label = effect_name.upper()[:12].ljust(12)
    set_effect_running(effect_name)
    print(f"{label} background start")

    tick = 1.0 / max(0.5, float(updates_per_second))
    loop = asyncio.get_event_loop()
    kitchen_delay = random.uniform(0.6, 1.0)
    cycle = _new_breath_cycle(
        loop.time(),
        inhale_range,
        top_pause_range,
        exhale_range,
        bottom_pause_range,
        min_pct_range,
        max_pct_range,
        shimmer_chance,
    )

    offsets = {
        bulb.ip: _breath_room_delay(bulb.ip, kitchen_delay) + _stable_bulb_offset(bulb.ip)
        for bulb in bulbs
    }

    try:
        while not effect_should_stop():
            now = loop.time()
            if now - cycle.start_time >= cycle.duration:
                cycle = _new_breath_cycle(
                    cycle.start_time + cycle.duration,
                    inhale_range,
                    top_pause_range,
                    exhale_range,
                    bottom_pause_range,
                    min_pct_range,
                    max_pct_range,
                    shimmer_chance,
                )

            for bulb in bulbs:
                phase = now - cycle.start_time - offsets[bulb.ip]
                amount = _breath_amount(phase, cycle)
                shimmer = _breath_shimmer(cycle, phase)
                bri = _lerp_int(cycle.low_bri, cycle.high_bri, amount)
                bri = max(1, min(255, int(round(bri * (1.0 + shimmer)))))
                peak = _cosine_ease((amount - 0.82) / 0.18) if amount > 0.82 else 0.0
                rgb = _lerp_rgb(cycle.low_rgb, cycle.high_rgb, amount)
                rgb = _lerp_rgb(rgb, cycle.peak_rgb, peak * 0.35)
                send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], scale_bri(bri))

            await asyncio.sleep(tick)
    finally:
        clear_effect_running()
        await close_all(bulbs)


async def sleep_breathe() -> None:
    await minotaur_breath(
        effect_name="sleep_breathe",
        inhale_range=(5.0, 7.0),
        top_pause_range=(0.6, 1.2),
        exhale_range=(8.0, 12.0),
        bottom_pause_range=(2.0, 4.0),
        min_pct_range=(1.0, 3.0),
        max_pct_range=(8.0, 18.0),
        shimmer_chance=0.18,
    )


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


PSYCHEDELIC_COLORS = [
    (255, 0, 190),    # hot magenta
    (130, 0, 255),    # electric violet
    (0, 80, 255),     # saturated blue
    (0, 255, 210),    # cyan
    (0, 255, 70),     # laser green
    (255, 255, 0),    # yellow
    (255, 90, 0),     # orange
    (255, 0, 45),     # red-pink
]


def _psychedelic_rand_rgb(previous: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    choices = [rgb for rgb in PSYCHEDELIC_COLORS if rgb != previous]
    return random.choice(choices)


async def psychedelic(
    min_wait: float = 0.35,
    max_wait: float = 1.15,
    base_bri: int = 190,
    bri_jitter: int = 45,
) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running("psychedelic")
    print("PSYCHEDELIC   background start")

    last_colors: dict[str, tuple[int, int, int]] = {}

    def _rand_bri() -> int:
        raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
        return scale_bri(max(80, min(255, raw)))

    def _send(bulb, rgb: tuple[int, int, int]) -> None:
        last_colors[bulb.ip] = rgb
        send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], _rand_bri())

    try:
        for i, bulb in enumerate(bulbs):
            _send(bulb, PSYCHEDELIC_COLORS[i % len(PSYCHEDELIC_COLORS)])
        await asyncio.sleep(0.35)

        while not effect_should_stop():
            if len(bulbs) > 1 and random.random() < 0.28:
                random.shuffle(bulbs)
                for bulb in bulbs:
                    _send(bulb, _psychedelic_rand_rgb(last_colors.get(bulb.ip)))
                    await asyncio.sleep(random.uniform(0.04, 0.16))
            else:
                bulb = random.choice(bulbs)
                _send(bulb, _psychedelic_rand_rgb(last_colors.get(bulb.ip)))

                if len(bulbs) > 1 and random.random() < 0.65:
                    other = random.choice([b for b in bulbs if b.ip != bulb.ip])
                    await asyncio.sleep(random.uniform(0.08, 0.25))
                    _send(other, _psychedelic_rand_rgb(last_colors.get(other.ip)))

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        clear_effect_running()
        await close_all(bulbs)


SLEEP_FLOW_COLORS = [
    (24, 4, 80),     # deep violet
    (8, 12, 72),     # midnight blue
    (35, 6, 95),     # muted purple
    (12, 26, 86),    # blue shadow
    (48, 10, 65),    # plum
    (6, 36, 58),     # dark teal-blue
]


def _soft_rgb_variant(rgb: tuple[int, int, int], spread: int = 10) -> tuple[int, int, int]:
    return tuple(
        max(0, min(255, channel + random.randint(-spread, spread)))
        for channel in rgb
    )


def _next_bulb_index(current: int, count: int) -> int:
    if count <= 1:
        return 0
    step = random.choice([1, 1, 1, 2])
    return (current + step) % count


async def sleep_flow(
    min_wait: float = 1.3,
    max_wait: float = 3.2,
    base_bri: int = 38,
    bri_jitter: int = 10,
) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running("sleep_flow")
    print("SLEEP_FLOW    background start")
    palette_idx = random.randrange(len(SLEEP_FLOW_COLORS))
    bulb_idx = random.randrange(len(bulbs))
    current_rgb = SLEEP_FLOW_COLORS[palette_idx]

    def _rand_bri(offset: int = 0) -> int:
        raw = int(base_bri) + int(offset) + random.randint(-int(bri_jitter), int(bri_jitter))
        return scale_bri(max(12, min(80, raw)))

    def _send(bulb, rgb: tuple[int, int, int], offset: int = 0) -> None:
        send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], _rand_bri(offset))

    try:
        for i, bulb in enumerate(bulbs):
            seed_rgb = _soft_rgb_variant(current_rgb, spread=8)
            _send(bulb, seed_rgb, offset=-(i % 3))
            await asyncio.sleep(0.25)

        while not effect_should_stop():
            if random.random() < 0.22:
                palette_idx = (palette_idx + 1) % len(SLEEP_FLOW_COLORS)
                current_rgb = SLEEP_FLOW_COLORS[palette_idx]

            bulb_idx = _next_bulb_index(bulb_idx, len(bulbs))
            rgb = _soft_rgb_variant(current_rgb, spread=10)
            _send(bulbs[bulb_idx], rgb)

            if len(bulbs) > 1 and random.random() < 0.45:
                await asyncio.sleep(random.uniform(0.25, 0.7))
                bulb_idx = _next_bulb_index(bulb_idx, len(bulbs))
                rgb = _soft_rgb_variant(current_rgb, spread=10)
                _send(bulbs[bulb_idx], rgb, offset=-3)

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

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
            idx = random.randrange(len(bulbs))
            await bulbs[idx].turn_on(PilotBuilder(brightness=scale_bri(_aurora_rand_bri(base_bri, bri_jitter)), rgb=_aurora_rand_rgb()))
            print(f"AURORA        reseed {bulbs[idx].ip}")

            if len(bulbs) > 1 and random.random() < 0.40:
                other = random.choice([i for i in range(len(bulbs)) if i != idx])
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
    Abyss palette - a dark candle flame: mostly violet/indigo with rare blue-white wick flares.
    """
    return _weighted_palette_choice([
        (36, (24, 58), (0, 5), (82, 145)),       # low violet body
        (26, (8, 30), (0, 6), (105, 175)),       # midnight indigo
        (18, (42, 82), (0, 7), (115, 190)),      # purple breathing edge
        (12, (4, 18), (8, 24), (95, 155)),       # cold blue shadow
        (6, (70, 118), (0, 8), (175, 245)),      # quick violet lick
        (2, (115, 165), (8, 24), (215, 255)),    # rare dark-candle wick flare
    ])


def _abyss_neighbor_rgb(rgb: tuple[int, int, int], spread: int = 20) -> tuple[int, int, int]:
    r, g, b = rgb
    return (
        _clamp(r + random.randint(-spread, spread), 0, 255),
        _clamp(g + random.randint(-4, 8), 0, 32),
        _clamp(b + random.randint(-spread, spread), 70, 255),
    )


def _lerp_int(start: int, end: int, amount: float) -> int:
    return int(round(start + (end - start) * amount))


def _weighted_palette_choice(palette: list[tuple[int, tuple[int, int], tuple[int, int], tuple[int, int]]]) -> tuple[int, int, int]:
    total = sum(weight for weight, _r, _g, _b in palette)
    pick = random.uniform(0, total)
    upto = 0.0
    for weight, red, green, blue in palette:
        upto += weight
        if pick <= upto:
            return (
                random.randint(red[0], red[1]),
                random.randint(green[0], green[1]),
                random.randint(blue[0], blue[1]),
            )
    _weight, red, green, blue = palette[-1]
    return (
        random.randint(red[0], red[1]),
        random.randint(green[0], green[1]),
        random.randint(blue[0], blue[1]),
    )


LAVA_LAMP_PALETTE = [
    (28, (50, 90), (0, 8), (78, 135)),       # dark violet wax
    (24, (82, 135), (0, 10), (105, 175)),    # plum glow
    (18, (115, 170), (8, 24), (44, 88)),     # wine red body
    (14, (25, 58), (6, 18), (112, 185)),     # indigo shadow
    (10, (135, 195), (32, 62), (35, 72)),    # dim ember edge
    (6, (145, 210), (0, 16), (145, 215)),    # slow magenta bloom
]


def _lava_lamp_rgb_variant(rgb: tuple[int, int, int], spread: int = 22) -> tuple[int, int, int]:
    r, g, b = rgb
    return (
        _clamp(r + random.randint(-spread, spread), 8, 220),
        _clamp(g + random.randint(-6, 12), 0, 70),
        _clamp(b + random.randint(-spread, spread), 28, 225),
    )


async def lava_lamp(base_bri: int = 46, bri_jitter: int = 16) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running("lava_lamp")
    print("LAVA_LAMP     background start")

    mood = {"rgb": _weighted_palette_choice(LAVA_LAMP_PALETTE)}
    room_moods: dict[str, tuple[int, int, int]] = {}

    def _room_key(bulb) -> str:
        return ROOM_BY_IP.get(bulb.ip, "ALL").lower()

    for bulb in bulbs:
        room = _room_key(bulb)
        room_moods.setdefault(room, _lava_lamp_rgb_variant(mood["rgb"], spread=20))

    def _rand_bri(warm_blob: bool = False) -> int:
        bump = random.randint(8, 24) if warm_blob else 0
        raw = int(base_bri) + bump + random.randint(-int(bri_jitter), int(bri_jitter))
        return max(10, min(95, int(scale_bri(raw))))

    def _send(bulb, rgb: tuple[int, int, int], bri: int) -> None:
        send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], bri)

    async def _mood_drift() -> None:
        while not effect_should_stop():
            if random.random() < 0.62:
                mood["rgb"] = _lava_lamp_rgb_variant(mood["rgb"], spread=20)
            else:
                mood["rgb"] = _weighted_palette_choice(LAVA_LAMP_PALETTE)
            await asyncio.sleep(random.uniform(3.5, 8.0))

    async def _room_drift(room: str, offset: float) -> None:
        await asyncio.sleep(offset)
        while not effect_should_stop():
            whole_rgb = mood["rgb"]
            current_rgb = room_moods[room]
            amount = random.uniform(0.42, 0.68)
            followed_rgb = (
                _lerp_int(current_rgb[0], whole_rgb[0], amount),
                _lerp_int(current_rgb[1], whole_rgb[1], amount),
                _lerp_int(current_rgb[2], whole_rgb[2], amount),
            )
            room_moods[room] = _lava_lamp_rgb_variant(followed_rgb, spread=18)
            await asyncio.sleep(random.uniform(1.8, 4.5))

    async def _bulb_drift(bulb, index: int) -> None:
        room = _room_key(bulb)
        current_rgb = _lava_lamp_rgb_variant(room_moods[room], spread=18 + (index % 3) * 4)
        current_bri = _rand_bri()
        _send(bulb, current_rgb, current_bri)
        await asyncio.sleep(random.uniform(0.1, 0.8))

        while not effect_should_stop():
            warm_blob = random.random() < 0.28
            target_rgb = _lava_lamp_rgb_variant(room_moods[room], spread=26 + (index % 4) * 5)
            target_bri = _rand_bri(warm_blob=warm_blob)
            steps = random.randint(5, 10)
            step_wait = random.uniform(0.22, 0.48)

            for step in range(1, steps + 1):
                if effect_should_stop():
                    return
                amount = step / steps
                rgb = (
                    _lerp_int(current_rgb[0], target_rgb[0], amount),
                    _lerp_int(current_rgb[1], target_rgb[1], amount),
                    _lerp_int(current_rgb[2], target_rgb[2], amount),
                )
                bri = _lerp_int(current_bri, target_bri, amount)
                _send(bulb, rgb, bri)
                await asyncio.sleep(step_wait)

            current_rgb = target_rgb
            current_bri = target_bri
            await asyncio.sleep(random.uniform(0.35, 1.6))

    try:
        tasks = [asyncio.create_task(_mood_drift())]
        tasks.extend(
            asyncio.create_task(_room_drift(room, offset=i * 0.45))
            for i, room in enumerate(sorted(room_moods))
        )
        tasks.extend(asyncio.create_task(_bulb_drift(bulb, i)) for i, bulb in enumerate(bulbs))
        await asyncio.gather(*tasks)
    finally:
        clear_effect_running()
        await close_all(bulbs)


DARK_ORGANIC_EFFECTS = {
    "smolder": {
        "base_bri": 52,
        "bri_jitter": 18,
        "min_wait": 7,
        "max_wait": 24,
        "follow_chance": 0.38,
        "palette": [
            (42, (65, 120), (6, 22), (8, 24)),      # oxblood
            (28, (105, 170), (28, 68), (8, 22)),    # dark ember amber
            (18, (55, 95), (0, 12), (38, 75)),      # burnt violet
            (12, (150, 210), (45, 88), (12, 30)),   # brief coal flare
        ],
    },
    "midnight_embers": {
        "base_bri": 45,
        "bri_jitter": 16,
        "min_wait": 8,
        "max_wait": 26,
        "follow_chance": 0.42,
        "palette": [
            (38, (45, 85), (0, 10), (70, 130)),     # deep purple
            (30, (12, 35), (0, 8), (95, 170)),      # indigo blue
            (20, (80, 125), (0, 12), (115, 185)),   # violet glow
            (12, (125, 185), (28, 58), (24, 48)),   # muted warm ember
        ],
    },
    "blue_coals": {
        "base_bri": 42,
        "bri_jitter": 14,
        "min_wait": 7,
        "max_wait": 22,
        "follow_chance": 0.35,
        "palette": [
            (42, (4, 18), (12, 34), (80, 150)),     # dark cobalt
            (30, (10, 36), (0, 12), (120, 205)),    # indigo
            (18, (35, 75), (0, 20), (95, 170)),     # blue violet
            (10, (0, 18), (45, 85), (105, 175)),    # dim cyan coal
        ],
    },
    "afterglow": {
        "base_bri": 48,
        "bri_jitter": 12,
        "min_wait": 16,
        "max_wait": 45,
        "follow_chance": 0.25,
        "palette": [
            (38, (95, 145), (30, 62), (28, 55)),    # ash copper
            (28, (70, 115), (18, 40), (45, 78)),    # wine shadow
            (22, (120, 175), (55, 88), (38, 70)),   # low amber
            (12, (45, 80), (20, 38), (55, 95)),     # smoky plum
        ],
    },
}


async def dark_organic(effect_name: str) -> None:
    spec = DARK_ORGANIC_EFFECTS[effect_name]
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError(f"{effect_name} requires at least 2 bulbs")

    set_effect_running(effect_name)
    label = effect_name.upper()[:12].ljust(12)
    print(f"{label} background start")

    def _rand_bri() -> int:
        raw = int(spec["base_bri"]) + random.randint(-int(spec["bri_jitter"]), int(spec["bri_jitter"]))
        return max(8, min(255, int(scale_bri(raw))))

    def _rand_rgb() -> tuple[int, int, int]:
        return _weighted_palette_choice(spec["palette"])

    try:
        for b in bulbs:
            rgb = _rand_rgb()
            send_raw_rgb(b.ip, rgb[0], rgb[1], rgb[2], _rand_bri())
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            idx = random.randrange(len(bulbs))
            rgb = _rand_rgb()
            send_raw_rgb(bulbs[idx].ip, rgb[0], rgb[1], rgb[2], _rand_bri())

            if len(bulbs) > 1 and random.random() < float(spec["follow_chance"]):
                other = random.choice([i for i in range(len(bulbs)) if i != idx])
                await asyncio.sleep(random.uniform(0.1, 0.8))
                rgb = _rand_rgb()
                send_raw_rgb(bulbs[other].ip, rgb[0], rgb[1], rgb[2], _rand_bri())

            await asyncio.sleep(random.uniform(float(spec["min_wait"]), float(spec["max_wait"])))

    finally:
        clear_effect_running()
        await close_all(bulbs)


async def campfire_low() -> None:
    await fireplace_organic(min_wait=8, max_wait=26, base_bri=55, bri_jitter=10, managed=True, effect_name="campfire_low")


async def deep_ocean_organic(min_wait=2.2, max_wait=7.5, base_bri=38, bri_jitter=16, managed=True):
    """Abyss effect - an organic dark candle in purple/blue flame colors."""
    bulbs = await get_bulbs()
    if len(bulbs) < 2:
        raise RuntimeError("abyss requires 2 bulbs")

    if managed:
        set_effect_running("abyss")
    print("ABYSS         background start")

    def _rand_bri(flare: bool = False):
        if flare:
            raw = int(base_bri) + random.randint(18, 38)
            return max(18, min(255, int(scale_bri(raw))))
        raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
        return max(8, min(255, int(scale_bri(raw))))

    def _send(b, rgb, bri):
        send_raw_rgb(b.ip, rgb[0], rgb[1], rgb[2], bri)

    try:
        states = {}
        mood_rgb = _deep_ocean_rand_rgb()
        for b in bulbs:
            rgb = _abyss_neighbor_rgb(mood_rgb)
            bri = _rand_bri()
            states[b.ip] = (rgb, bri)
            _send(b, rgb, bri)
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            if random.random() < 0.45:
                mood_rgb = _abyss_neighbor_rgb(mood_rgb, spread=14)
            else:
                mood_rgb = _deep_ocean_rand_rgb()

            targets = {}
            for b in bulbs:
                flare = random.random() < 0.07
                targets[b.ip] = (_abyss_neighbor_rgb(mood_rgb), _rand_bri(flare=flare))

            steps = random.randint(4, 7)
            step_wait = random.uniform(0.22, 0.48)
            for step in range(1, steps + 1):
                if effect_should_stop():
                    break
                amount = step / steps
                send_order = list(bulbs)
                random.shuffle(send_order)
                for b in send_order:
                    start_rgb, start_bri = states[b.ip]
                    target_rgb, target_bri = targets[b.ip]
                    rgb = (
                        _lerp_int(start_rgb[0], target_rgb[0], amount),
                        _lerp_int(start_rgb[1], target_rgb[1], amount),
                        _lerp_int(start_rgb[2], target_rgb[2], amount),
                    )
                    bri = _lerp_int(start_bri, target_bri, amount)
                    _send(b, rgb, bri)
                    await asyncio.sleep(random.uniform(0.015, 0.06))
                await asyncio.sleep(step_wait)

            states.update(targets)
            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        if managed:
            clear_effect_running()
        await close_all(bulbs)



NEON_RAIN_PALETTE = [
    (255, 0, 170),
    (120, 0, 255),
    (0, 210, 255),
    (0, 90, 255),
]

DEEP_SPACE_PALETTE = [
    (6, 4, 42),
    (18, 8, 80),
    (40, 8, 115),
    (3, 20, 78),
    (120, 160, 255),
]

MOONLIGHT_PALETTE = [
    (120, 160, 220),
    (160, 205, 255),
    (90, 125, 190),
    (190, 220, 255),
]

SYNTHWAVE_PALETTE = [
    (255, 0, 150),
    (95, 0, 255),
    (0, 180, 255),
    (255, 80, 0),
]

BIOHAZARD_PALETTE = [
    (90, 255, 30),
    (160, 255, 0),
    (255, 180, 0),
    (25, 120, 0),
]

UNDERWATER_RUINS_PALETTE = [
    (0, 45, 80),
    (0, 90, 120),
    (0, 125, 150),
    (15, 70, 110),
    (70, 170, 190),
]

async def palette_drift(effect_name: str, palette: list[tuple[int, int, int]], base_bri: int, bri_jitter: int, min_wait: float, max_wait: float) -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running(effect_name)

    def _rand_bri() -> int:
        raw = int(base_bri) + random.randint(-int(bri_jitter), int(bri_jitter))
        return max(8, min(255, int(scale_bri(raw))))

    try:
        for i, bulb in enumerate(bulbs):
            rgb = palette[i % len(palette)]
            send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], _rand_bri())
        await asyncio.sleep(0.4)

        while not effect_should_stop():
            bulb = random.choice(bulbs)
            rgb = random.choice(palette)
            rgb = _soft_rgb_variant(rgb, spread=14)
            send_raw_rgb(bulb.ip, rgb[0], rgb[1], rgb[2], _rand_bri())

            if len(bulbs) > 1 and random.random() < 0.45:
                await asyncio.sleep(random.uniform(0.08, 0.5))
                other = random.choice([b for b in bulbs if b.ip != bulb.ip])
                rgb = random.choice(palette)
                rgb = _soft_rgb_variant(rgb, spread=14)
                send_raw_rgb(other.ip, rgb[0], rgb[1], rgb[2], _rand_bri())

            await asyncio.sleep(random.uniform(float(min_wait), float(max_wait)))

    finally:
        clear_effect_running()
        await close_all(bulbs)

async def neon_rain() -> None:
    await palette_drift("neon_rain", NEON_RAIN_PALETTE, 95, 28, 0.55, 1.7)

async def deep_space() -> None:
    await palette_drift("deep_space", DEEP_SPACE_PALETTE, 34, 18, 2.5, 7.0)

async def moonlight() -> None:
    await palette_drift("moonlight", MOONLIGHT_PALETTE, 58, 14, 3.0, 8.5)

async def synthwave() -> None:
    await palette_drift("synthwave", SYNTHWAVE_PALETTE, 120, 36, 0.8, 2.2)

async def biohazard() -> None:
    await palette_drift("biohazard", BIOHAZARD_PALETTE, 90, 34, 0.45, 1.4)

async def underwater_ruins() -> None:
    await palette_drift("underwater_ruins", UNDERWATER_RUINS_PALETTE, 48, 18, 1.8, 5.8)

async def arc_reactor() -> None:
    bulbs = await get_bulbs()
    if not bulbs:
        return

    set_effect_running("arc_reactor")
    rgb = (80, 235, 255)

    try:
        while not effect_should_stop():
            await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(55), rgb=rgb)) for b in bulbs])
            await asyncio.sleep(0.8)
            await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(150), rgb=rgb)) for b in bulbs])
            await asyncio.sleep(0.18)
            await asyncio.gather(*[b.turn_on(PilotBuilder(brightness=scale_bri(85), rgb=rgb)) for b in bulbs])
            await asyncio.sleep(1.4)
    finally:
        clear_effect_running()
        await close_all(bulbs)

# --------------------------------------------------
# BACKGROUND DISPATCH
# --------------------------------------------------

async def run_background(cmd: str, args: list[str]) -> None:
    _install_signal_handlers()

    if cmd in CUSTOM_SCENES and _scene_uses_background(cmd):
        await run_custom_scene(cmd)
        save_last_mode(cmd, active_group())
        return

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

    if cmd == "breathe":
        await minotaur_breath(effect_name="breathe")
        save_last_mode(cmd, active_group())
        return

    if cmd == "sleep_breathe":
        await sleep_breathe()
        save_last_mode("sleep_breathe", active_group())
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

    if cmd == "psychedelic":
        await psychedelic()
        save_last_mode("psychedelic", active_group())
        return

    if cmd == "sleep_flow":
        await sleep_flow()
        save_last_mode("sleep_flow", active_group())
        return

    if cmd == "lava_lamp":
        await lava_lamp()
        save_last_mode("lava_lamp", active_group())
        return

    if cmd in DARK_ORGANIC_EFFECTS:
        await dark_organic(cmd)
        save_last_mode(cmd, active_group())
        return

    if cmd == "campfire_low":
        await campfire_low()
        save_last_mode("campfire_low", active_group())
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
        await hearth()
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

    if cmd == "neon_rain":
        await neon_rain()
        save_last_mode("neon_rain", active_group())
        return

    if cmd == "deep_space":
        await deep_space()
        save_last_mode("deep_space", active_group())
        return

    if cmd == "moonlight":
        await moonlight()
        save_last_mode("moonlight", active_group())
        return

    if cmd == "synthwave":
        await synthwave()
        save_last_mode("synthwave", active_group())
        return

    if cmd == "biohazard":
        await biohazard()
        save_last_mode("biohazard", active_group())
        return

    if cmd == "underwater_ruins":
        await underwater_ruins()
        save_last_mode("underwater_ruins", active_group())
        return

    if cmd == "arc_reactor":
        await arc_reactor()
        save_last_mode("arc_reactor", active_group())
        return

    if cmd == "alert_police":
        secs = float(args[0]) if len(args) > 0 else 15.0
        stop_effects_affecting_target()
        await alert_police(seconds=secs)
        return

    if cmd == "alert_pulse":
        secs = float(args[0]) if len(args) > 0 else 15.0

        if ALERT_PULSE_TOGGLE.exists():
            try:
                ALERT_PULSE_TOGGLE.unlink()
            except FileNotFoundError:
                pass
            stop_effects_affecting_target()
            print("ALERT_PULSE   stopped")
            return

        ALERT_PULSE_TOGGLE.write_text("1")
        stop_effects_affecting_target()
        await alert_pulse(seconds=secs)
        return

    raise RuntimeError(f"Unknown background effect: {cmd}")


# --------------------------------------------------
# COOK MODE
# --------------------------------------------------

def _cook_log(message: str, **fields) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    parts = [f"{stamp}", f"pid={os.getpid()}", message]
    for key, value in fields.items():
        parts.append(f"{key}={value!r}")
    try:
        with COOK_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(" ".join(parts) + "\n")
    except Exception:
        pass


def _effect_log(message: str, **fields) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    parts = [stamp, f"pid={os.getpid()}", message]
    for key, value in fields.items():
        parts.append(f"{key}={value!r}")
    try:
        with EFFECT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(" ".join(parts) + "\n")
    except Exception:
        pass


def _audit_log(event: str, **fields) -> None:
    def _proc_cmdline(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except Exception:
            return None

    def _proc_ppid(pid: int) -> int | None:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("PPid:"):
                    return int(line.split()[1])
        except Exception:
            return None
        return None

    record = {
        "ts": time.time(),
        "event": event,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "argv": sys.argv[:],
    }

    parent_cmd = _proc_cmdline(os.getppid())
    if parent_cmd:
        record["ppid_cmdline"] = parent_cmd

    process_tree = []
    seen_pids = set()
    pid = os.getppid()
    for _ in range(5):
        if not pid or pid in seen_pids or pid <= 1:
            break
        seen_pids.add(pid)
        cmdline = _proc_cmdline(pid)
        if cmdline:
            process_tree.append({"pid": pid, "cmdline": cmdline})
        pid = _proc_ppid(pid)

    if process_tree:
        record["process_tree"] = process_tree

    env_snapshot = {
        key: os.getenv(key)
        for key in (
            "LIGHTS_SOURCE",
            "LIGHTS_API_ROUTE",
            "LIGHTS_DAEMON_ACTION",
            "LIGHTS_DASHBOARD_ACTION",
            "LIGHTS_MQTT_TOPIC",
            "SSH_CONNECTION",
            "TERM",
            "USER",
        )
        if os.getenv(key) is not None
    }
    if env_snapshot:
        record["env"] = env_snapshot

    for key, value in fields.items():
        record[key] = value

    try:
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def _load_cook_press_state() -> dict:
    try:
        raw = COOK_DEBOUNCE_FILE.read_text().strip()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            return {"time": float(raw), "branch": "legacy"}
        except Exception:
            return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _save_cook_press_state(branch: str) -> None:
    data = {"time": time.time(), "branch": branch, "pid": os.getpid()}
    try:
        _write_json_atomic(COOK_DEBOUNCE_FILE, data)
    except Exception:
        pass


def _cook_restore_mode(saved_kitchen_mode: str | None, saved_all_mode: str | None) -> str:
    temporary_cook_modes = {"soft", COOK_MODE}
    if saved_kitchen_mode and saved_kitchen_mode not in temporary_cook_modes:
        return saved_kitchen_mode
    if saved_kitchen_mode in temporary_cook_modes and saved_all_mode:
        _cook_log("restore skipped cook mode", mode=saved_kitchen_mode, fallback=saved_all_mode)
        return saved_all_mode
    return saved_kitchen_mode or saved_all_mode or "golden_white"


async def cook_toggle() -> None:
    try:
        COOK_LOCK_FILE.touch(exist_ok=True)
        lock_fh = COOK_LOCK_FILE.open("r+")
    except Exception as e:
        _cook_log("lock open failed", error=type(e).__name__)
        print("COOK         busy")
        return

    with lock_fh:
        deadline = time.monotonic() + 1.5
        while True:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _cook_log("lock timeout")
                    print("COOK         busy")
                    return
                time.sleep(0.05)

        try:
            await _cook_toggle_locked()
        finally:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            except Exception:
                pass


async def _cook_toggle_locked() -> None:
    """
    Toggle kitchen cooking mode.

    First run:
      - saves current mode/effect info
      - if an all-room effect is running, moves it to entryway only
      - sets kitchen to the cook preset

    Second run:
      - restores the prior kitchen/all mode or effect
    """
    old_group = active_group()
    _cook_log("start", active_group=old_group, override_exists=COOK_OVERRIDE_FILE.exists())

    def _recent_press(branch: str, max_age: float = 0.6) -> float | None:
        press_state = _load_cook_press_state()
        last_branch = press_state.get("branch")
        last_time = float(press_state.get("time") or 0.0)
        age = time.time() - last_time if last_time else None
        if last_branch == branch and age is not None and age < max_age:
            return age
        return None

    if COOK_OVERRIDE_FILE.exists():
        age = _recent_press("activate")
        if age is not None:
            _cook_log("restore ignored duplicate", age=round(age, 3))
            print("COOK         ignored duplicate restore")
            return

        try:
            data = json.loads(COOK_OVERRIDE_FILE.read_text())
        except Exception as e:
            _cook_log("restore invalid override", error=type(e).__name__)
            data = {}

        _safe_unlink(COOK_OVERRIDE_FILE)
        _save_cook_press_state("restore")

        saved_effect = data.get("effect")
        saved_effect_group = data.get("effect_group")
        saved_kitchen_mode = data.get("last_kitchen")
        saved_all_mode = data.get("last_all")
        _cook_log(
            "restore branch",
            saved_effect=saved_effect,
            saved_effect_group=saved_effect_group,
            saved_kitchen_mode=saved_kitchen_mode,
            saved_all_mode=saved_all_mode,
        )

        stop_running_effect("kitchen")

        if saved_effect:
            save_pending_mode(saved_effect, "kitchen")
            launch_background(saved_effect, "kitchen")
            save_last_mode(saved_effect, "kitchen")
            _cook_log("restored effect", effect=saved_effect, original_group=saved_effect_group, group="kitchen")
            print("COOK         restored")
            _set_active_group(old_group)
            return

        restore_mode = _cook_restore_mode(saved_kitchen_mode, saved_all_mode)
        if restore_mode in BACKGROUND_EFFECTS:
            save_pending_mode(restore_mode, "kitchen")
            launch_background(restore_mode, "kitchen")
            save_last_mode(restore_mode, "kitchen")
            _cook_log("restored saved background mode", mode=restore_mode, group="kitchen")
        elif restore_mode in CUSTOM_SCENES:
            _set_active_group("kitchen")
            try:
                save_pending_mode(restore_mode, "kitchen")
                await run_or_launch_custom_scene(restore_mode, "kitchen" if _scene_uses_background(restore_mode) else None)
                save_last_mode(restore_mode, "kitchen")
            finally:
                _set_active_group(old_group)
            _cook_log("restored saved custom scene", mode=restore_mode, group="kitchen")
        else:
            _set_active_group("kitchen")
            try:
                save_pending_mode(restore_mode, "kitchen")
                await turn_on(restore_mode)
                save_last_mode(restore_mode, "kitchen")
            finally:
                _set_active_group(old_group)
            _cook_log("restored saved preset", mode=restore_mode, group="kitchen")

        print("COOK         restored")
        _set_active_group(old_group)
        return

    age = _recent_press("restore")
    if age is not None:
        _cook_log("activate ignored duplicate", age=round(age, 3))
        print("COOK         ignored duplicate activate")
        return

    saved = {
        "effect": load_effect_affecting_target("kitchen"),
        "effect_group": effect_group_affecting_target("kitchen"),
        "last_kitchen": load_last_mode("kitchen"),
        "last_all": load_last_mode(None),
    }
    _write_json_atomic(COOK_OVERRIDE_FILE, saved)
    _save_cook_press_state("activate")
    _cook_log(
        "activate branch wrote override",
        saved_effect=saved["effect"],
        saved_effect_group=saved["effect_group"],
        saved_kitchen_mode=saved["last_kitchen"],
        saved_all_mode=saved["last_all"],
    )

    if saved["effect"]:
        if saved["effect_group"] == "all":
            stop_running_effect("all")
            launch_background(saved["effect"], "entryway")
            save_last_mode(saved["effect"], "entryway")
            _cook_log("moved all effect to entryway", effect=saved["effect"])
        elif saved["effect_group"] == "kitchen":
            stop_running_effect("kitchen")
            _cook_log("stopped kitchen effect for cook", effect=saved["effect"])

    _set_active_group("kitchen")
    try:
        save_pending_mode(COOK_MODE, "kitchen")
        # Reset to a clean warm-white pilot first so no residual RGB/effect
        # color can bleed through, then dim down to the cook target. This
        # mirrors the verified-good manual sequence: `warm` then `dim`.
        await turn_on("warm")
        await turn_on(COOK_MODE)
    finally:
        _set_active_group(old_group)
    _cook_log("kitchen set to cook mode", mode=COOK_MODE)

    print(f"COOK         kitchen {COOK_MODE}")

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
    print("  cook                Toggle kitchen cooking mode")
    print("  dim [delta]")
    print("  dim <B1|B2> <delta>")
    print("  alert [seconds]       Pulse alert (toggle on/off)")
    print("  alert_pulse | alert-pulse [seconds] Pulse alert (toggle on/off)")
    print("  alert_police | alert-police [seconds] Police-style alert")
    print("  fade <preset> <seconds>")
    print("  scene <name>        Run a custom multi-step scene")
    print("  b1 <preset> | b2 <preset> | duo <preset1> <preset2>")
    print("  snapshot save [name] | snapshot load [name] | snapshot list")
    print("")
    print("Custom scenes:")
    for name in sorted(CUSTOM_SCENES):
        print(f"  {name}")
    print("")
    print("Background effects:")
    for name in sorted(BACKGROUND_EFFECTS):
        print(f"  {name}")
    print("")
    print("Static presets:")
    for name in sorted(PRESETS.keys()):
        if name not in BACKGROUND_EFFECTS and name != "romance":
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
        bg_cmd = _normalize_cmd(argv[1])
        bg_cmd = COMMAND_ALIASES.get(bg_cmd, bg_cmd)
        rest = argv[2:]
        group, rest = _maybe_consume_group(rest)
        _set_active_group(group)
        _audit_log("invoke", phase="bg", group=active_group() or "all", cmd=bg_cmd, args=rest)
        if DRY_RUN:
            print(f"DRY_RUN      bg={bg_cmd} group={active_group() or 'all'} args={rest}")
            return
        _effect_log("START", cmd=bg_cmd, group=active_group() or "all", args=rest)
        error = None
        try:
            await run_background(bg_cmd, rest)
        except asyncio.CancelledError:
            error = "cancelled"
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _audit_log("bg_error", group=active_group() or "all", cmd=bg_cmd, error=error)
            _effect_log("ERROR", cmd=bg_cmd, group=active_group() or "all", error=error)
            raise
        finally:
            _audit_log("bg_exit", group=active_group() or "all", cmd=bg_cmd, error=error)
            _effect_log("EXIT", cmd=bg_cmd, group=active_group() or "all", error=error)
        return

    # Optional group prefix (normal CLI)
    group, argv = _maybe_consume_group(argv)
    _set_active_group(group)
    group_for_bg = group if group and group != "all" else None

    # If user typed only a group, show status for that group
    if not argv:
        if DRY_RUN:
            print(f"DRY_RUN      cmd=status group={active_group() or 'all'} args=[]")
            return
        await show_status()
        return

    cmd = _normalize_cmd(argv[0])
    cmd = COMMAND_ALIASES.get(cmd, cmd)
    _audit_log("invoke", phase="cli", group=active_group() or "all", cmd=cmd, args=argv[1:])

    if cmd in {"help", "-h", "--help", "?"}:
        print_help()
        return

    if cmd in {"current-mode", "current_mode"}:
        print(",".join(current_mode_names()))
        return

    if DRY_RUN:
        print(f"DRY_RUN      cmd={cmd} group={active_group() or 'all'} args={argv[1:]}")
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
        stop_effects_affecting_target()

        mode = load_last_mode_for_target() or "golden_white"
        save_pending_mode(mode, active_group())

        if mode in BACKGROUND_EFFECTS:
            launch_background(mode, group_for_bg)
            emit_effect_started(mode, command=cmd)
            return

        if mode in CUSTOM_SCENES:
            await run_or_launch_custom_scene(mode, group_for_bg)
            if _scene_uses_background(mode):
                emit_effect_started(mode, command=cmd)
            else:
                emit_scene_changed(mode, command=cmd)
            return

        await turn_on(mode)
        emit_scene_changed(mode, command=cmd)
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
        save_pending_mode("off", active_group())
        stop_effects_affecting_target()
        await turn_off()
        emit_lights_off(command=cmd)
        return

    if cmd == "toggle":
        # Optional mode override: lights kitchen toggle cozy
        toggle_mode = _normalize_cmd(argv[1]) if len(argv) > 1 else None

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
            save_pending_mode("off", active_group())
            stop_effects_affecting_target()
            await turn_off()
            emit_lights_off(command=cmd)
        else:
            mode = toggle_mode or load_last_mode_for_target() or "golden_white"
            save_pending_mode(mode, active_group())
            if mode in BACKGROUND_EFFECTS:
                launch_background(mode, group_for_bg)
                emit_effect_started(mode, command=cmd)
            elif mode in CUSTOM_SCENES:
                await run_or_launch_custom_scene(mode, group_for_bg)
                if _scene_uses_background(mode):
                    emit_effect_started(mode, command=cmd)
                else:
                    emit_scene_changed(mode, command=cmd)
            else:
                await turn_on(mode)
                emit_scene_changed(mode, command=cmd)
        return

    if cmd == "status":
        await show_status()
        return

    if cmd == "stop":
        stopped_effect = load_effect_affecting_target()
        try:
            ALERT_PULSE_TOGGLE.unlink()
        except FileNotFoundError:
            pass
        stop_effects_affecting_target()
        clear_pending_mode()
        await asyncio.sleep(0.25)
        emit_effect_stopped(stopped_effect, command=cmd)
        return

    if cmd == "cook":
        await cook_toggle()
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
            stop_effects_affecting_target()
            print("ALERT_PULSE   stopped")
            emit_effect_stopped("alert_pulse", command=cmd)
            return

        # Otherwise start it
        ALERT_PULSE_TOGGLE.write_text("1")
        save_pending_mode("alert_pulse", active_group())
        stop_effects_affecting_target()
        emit_effect_started("alert_pulse", command=cmd)
        await alert_pulse(seconds=secs)
        return

    if cmd == "alert_police":
        secs = float(argv[1]) if len(argv) > 1 else 15.0
        save_pending_mode("alert_police", active_group())
        stop_effects_affecting_target()
        emit_effect_started("alert_police", command=cmd)
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
            stop_effects_affecting_target()
            print("ALERT_PULSE   stopped")
            emit_effect_stopped("alert_pulse", command=cmd)
            return

        # Otherwise start it
        ALERT_PULSE_TOGGLE.write_text("1")
        save_pending_mode("alert_pulse", active_group())
        stop_effects_affecting_target()
        emit_effect_started("alert_pulse", command=cmd)
        await alert_pulse(seconds=secs)
        return


    # ---------------- dim ----------------
    if cmd == "dim":
        if len(argv) == 1:
            delta = -40
            if dim_running_effect(delta):
                return
            await dim_adjust("ALL", delta)
            return

        if len(argv) == 2:
            delta = int(argv[1])
            if dim_running_effect(delta):
                return
            await dim_adjust("ALL", delta)
            return

        if len(argv) == 3:
            raw_target = argv[1].strip().lower()
            delta = int(argv[2])

            if raw_target in {"b1", "1"}:
                if dim_running_effect(delta):
                    return
                await dim_adjust("B1", delta)
                return
            if raw_target in {"b2", "2"}:
                if dim_running_effect(delta):
                    return
                await dim_adjust("B2", delta)
                return

            # still allow the old explicit form
            target = argv[1].upper()
            if dim_running_effect(delta):
                return
            await dim_adjust(target, delta)
            return

        raise SystemExit("Usage: lights dim [delta] | lights dim <B1|B2> <delta>")

    # ---------------- fade ----------------
    if cmd == "fade":
        if len(argv) != 3:
            raise SystemExit("Usage: lights fade <preset> <seconds>")
        mode = _normalize_cmd(argv[1])
        stop_effects_affecting_target()
        save_pending_mode(mode, active_group())
        await fade_to(mode, float(argv[2]))
        save_last_mode(mode, active_group())
        emit_scene_changed(mode, command=cmd)
        return

    # ---------------- custom scenes ----------------
    if cmd == "scene":
        if len(argv) != 2:
            raise SystemExit("Usage: lights scene <name>")
        scene_name = _normalize_cmd(argv[1])
        if scene_name not in CUSTOM_SCENES:
            raise SystemExit(f"Unknown custom scene: {argv[1]}")
        stop_effects_affecting_target()
        save_pending_mode(scene_name, active_group())
        await run_or_launch_custom_scene(scene_name, group_for_bg)
        save_last_mode(_scene_result_mode(scene_name), active_group())
        if _scene_uses_background(scene_name):
            emit_effect_started(scene_name, command=cmd)
        else:
            emit_scene_changed(scene_name, command=cmd)
        return

    # ---------------- bulb targeting ----------------
    if cmd == "b1":
        if len(argv) != 2:
            raise SystemExit("Usage: lights b1 <preset>")
        mode = _normalize_cmd(argv[1])
        stop_effects_affecting_target()
        save_pending_mode(mode, active_group())
        await turn_on_b1(mode)
        save_last_mode(mode, active_group())
        emit_scene_changed(mode, command=cmd, target_name="b1")
        return

    if cmd == "b2":
        if len(argv) != 2:
            raise SystemExit("Usage: lights b2 <preset>")
        mode = _normalize_cmd(argv[1])
        stop_effects_affecting_target()
        save_pending_mode(mode, active_group())
        await turn_on_b2(mode)
        save_last_mode(mode, active_group())
        emit_scene_changed(mode, command=cmd, target_name="b2")
        return

    if cmd == "duo":
        if len(argv) != 3:
            raise SystemExit("Usage: lights duo <preset1> <preset2>")
        mode_b1 = _normalize_cmd(argv[1])
        mode_b2 = _normalize_cmd(argv[2])
        stop_effects_affecting_target()
        save_pending_mode(mode_b1, active_group())
        await turn_duo(mode_b1, mode_b2)
        save_last_mode(mode_b1, active_group())
        emit_scene_changed(f"{mode_b1},{mode_b2}", command=cmd, target_name="duo")
        return

    # ---------------- background effects ----------------
    if cmd in BACKGROUND_EFFECTS:
        launch_background(cmd, group_for_bg)
        save_last_mode(cmd, active_group())
        emit_effect_started(cmd, command=cmd)
        return

    # ---------------- custom scene aliases ----------------
    if cmd in CUSTOM_SCENES:
        stop_effects_affecting_target()
        save_pending_mode(cmd, active_group())
        await run_or_launch_custom_scene(cmd, group_for_bg)
        save_last_mode(_scene_result_mode(cmd), active_group())
        if _scene_uses_background(cmd):
            emit_effect_started(cmd, command=cmd)
        else:
            emit_scene_changed(cmd, command=cmd)
        return

    # ---------------- static presets ----------------
    if cmd not in PRESETS:
        print(f"ERROR        Unknown command or preset: {cmd}")
        print("")
        print_help()
        raise SystemExit(2)

    stop_effects_affecting_target()
    save_pending_mode(cmd, active_group())
    await turn_on(cmd)
    save_last_mode(cmd, active_group())
    emit_scene_changed(cmd, command=cmd)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            command = _normalize_cmd(sys.argv[1]) if len(sys.argv) > 1 else "unknown"
            emit_lights_error(command, str(exc))
        raise
    except Exception as exc:
        command = _normalize_cmd(sys.argv[1]) if len(sys.argv) > 1 else "unknown"
        emit_lights_error(command, f"{type(exc).__name__}: {exc}")
        raise
