#!/home/pi/venvs/wiz/bin/python

import asyncio
import curses
import time
import subprocess
import threading
import queue
import signal

from typing import List, Dict, Any, Tuple, Optional

import lights as L

# Cache getPilot results so UI doesn't stall on UDP timeouts
_PILOT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PILOT_TTL_SEC = 30.0
_PILOT_TIMEOUT_SEC = 0.12

def room_slot_label(ip: str) -> str:
    """
    Return a stable label like KITCHEN-1 / KITCHEN-2 / ENTRYWAY-1 / ENTRYWAY-2.
    Uses ROOM_BY_IP from lights.py and sorts IPs within each room for stable numbering.
    """
    try:
        room_by_ip = getattr(L, "ROOM_BY_IP", {}) or {}
        room = room_by_ip.get(ip, "UNKNOWN")
        ips_in_room = sorted([x for x, r in room_by_ip.items() if r == room])
        if ip in ips_in_room:
            idx = ips_in_room.index(ip) + 1
            return f"{room}-{idx}"
        return room
    except Exception:
        return "UNKNOWN"

# -----------------------------
# Background command runner
# -----------------------------

class CmdRunner:
    def __init__(self, lights_cmd: str = "lights"):
        self.lights_cmd = lights_cmd
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.q: "queue.Queue[str]" = queue.Queue()
        self.running = False
        self.last_line = ""
        self.current_cmd = ""

    def start(self, args: List[str]) -> None:
        # Cancel any existing command first
        if self.running:
            self.cancel()

        cmd = [self.lights_cmd] + args
        self.current_cmd = " ".join(cmd)

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.running = False
            self.last_line = f"Launch error: {type(e).__name__}: {e}"
            return

        self.running = True
        self.last_line = f"Started: {self.current_cmd}"

        def _reader():
            try:
                assert self.proc is not None
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    s = line.rstrip("\n")
                    if s:
                        self.q.put(s)
                rc = self.proc.wait()
                self.q.put(f"Done (rc={rc})")
            except Exception as e:
                self.q.put(f"Runner error: {type(e).__name__}: {e}")
            finally:
                self.running = False

        self.thread = threading.Thread(target=_reader, daemon=True)
        self.thread.start()

    def poll_lines(self, max_lines: int = 20) -> List[str]:
        lines = []
        for _ in range(max_lines):
            try:
                lines.append(self.q.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.last_line = lines[-1]
        return lines

    def poke(self, args: List[str]) -> None:
        """
        Run a short one-off command without cancelling the currently running command.
        Used for things like dim adjustments while an animation is running.
        """
        cmd = [self.lights_cmd] + args
        cmd_str = " ".join(cmd)

        def _run():
            try:
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert p.stdout is not None
                for line in p.stdout:
                    s = line.rstrip("\n")
                    if s:
                        self.q.put(s)
                rc = p.wait()
                self.q.put(f"Done (rc={rc}) [{cmd_str}]")
            except Exception as e:
                self.q.put(f"Poke error: {type(e).__name__}: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def cancel(self) -> None:
        if not self.proc:
            self.running = False
            return
        try:
            # SIGINT so your lights script can clean up (finally blocks, temp files, etc.)
            self.proc.send_signal(signal.SIGINT)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.last_line = "Cancel requested"

MULTI_COLOR_NAMES: Dict[str, Tuple[Tuple[int,int,int], Tuple[int,int,int]]] = {
    # two-color alternators
    "police_siren": ((255, 0, 0), (0, 120, 255)),
    "tiffany": ((248, 229, 201), (241, 193, 89)),
    "christmas": ((255, 0, 0), (0, 255, 0)),
    "halloween": ((255, 80, 0), (180, 0, 255)),
}

def init_multicolor_from_lights() -> None:
    """
    Auto-add duo presets (like tiffany) to MULTI_COLOR_NAMES by reading lights.py PRESETS.
    Uses PRESET_RGB_HINTS from lights.py to color each side.
    """
    try:
        presets = getattr(L, "PRESETS", {}) or {}
        for name, preset in presets.items():
            if not isinstance(preset, dict):
                continue
            duo = preset.get("duo")
            if not (isinstance(duo, (tuple, list)) and len(duo) == 2):
                continue
            p1, p2 = str(duo[0]), str(duo[1])
            MULTI_COLOR_NAMES[name] = (get_preset_rgb(p1), get_preset_rgb(p2))
    except Exception:
        pass


VIRTUAL_MENU_ACTIONS: Dict[str, List[str]] = {
}

# -----------------------------
# Color helpers (preset coloring)
# -----------------------------

_COLOR_STATE = {
    "enabled": False,
    "colors": 0,
    "pairs": 0,
    "supports_256": False,
    "pair_cache": {},
    "next_pair_id": 20,
}

def init_colors():
    if not curses.has_colors():
        _COLOR_STATE["enabled"] = False
        return False

    curses.start_color()
    try:
        curses.use_default_colors()
    except Exception:
        pass

    _COLOR_STATE["colors"] = getattr(curses, "COLORS", 0) or 0
    _COLOR_STATE["pairs"] = getattr(curses, "COLOR_PAIRS", 0) or 0

    # Important: some terminals report COLORS=256 but have a low COLOR_PAIRS count.
    # If pairs are low, dynamic 256-pair allocation hits the ceiling and things go grey.
    _COLOR_STATE["supports_256"] = (_COLOR_STATE["colors"] >= 256 and _COLOR_STATE["pairs"] >= 256)

    curses.init_pair(1, curses.COLOR_MAGENTA, -1)  # header
    curses.init_pair(2, curses.COLOR_CYAN, -1)     # section
    curses.init_pair(3, curses.COLOR_GREEN, -1)    # good
    curses.init_pair(4, curses.COLOR_RED, -1)      # bad
    curses.init_pair(5, curses.COLOR_YELLOW, -1)   # accent
    curses.init_pair(6, curses.COLOR_WHITE, -1)    # dim-ish
    curses.init_pair(7, curses.COLOR_WHITE, -1)    # normal

    _COLOR_STATE["enabled"] = True
    return True

def cpair(n: int) -> int:
    return curses.color_pair(n)

def rgb_to_ansi256(r: int, g: int, b: int) -> int:
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))

    if r == g == b:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + int((r - 8) / 10)

    r6 = int(r / 51)
    g6 = int(g / 51)
    b6 = int(b / 51)
    return 16 + 36 * r6 + 6 * g6 + b6

def get_dynamic_pair_for_fg(fg_color_index: int) -> int:
    cache = _COLOR_STATE["pair_cache"]
    if fg_color_index in cache:
        return curses.color_pair(cache[fg_color_index])

    pair_id = _COLOR_STATE["next_pair_id"]
    if _COLOR_STATE["pairs"] and pair_id >= _COLOR_STATE["pairs"]:
        return cpair(7)

    curses.init_pair(pair_id, fg_color_index, -1)
    cache[fg_color_index] = pair_id
    _COLOR_STATE["next_pair_id"] += 1
    return curses.color_pair(pair_id)

def closest_basic_color(r: int, g: int, b: int) -> int:
    if r > 200 and g > 200 and b > 200:
        return curses.COLOR_WHITE
    if r > 200 and g > 200 and b < 120:
        return curses.COLOR_YELLOW
    if r > 180 and b > 180 and g < 120:
        return curses.COLOR_MAGENTA
    if g > 180 and b > 180 and r < 120:
        return curses.COLOR_CYAN
    if r >= g and r >= b:
        return curses.COLOR_RED
    if g >= r and g >= b:
        return curses.COLOR_GREEN
    return curses.COLOR_BLUE


# Prefer colors defined in lights.py (single source of truth).
# If lights.py does not provide them for some reason, we will still have a tiny heuristic fallback.
PRESET_RGB_HINTS: Dict[str, Tuple[int, int, int]] = {}

try:
    src = getattr(L, "PRESET_RGB_HINTS", None)
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, (tuple, list)) and len(v) == 3:
                PRESET_RGB_HINTS[str(k)] = (int(v[0]), int(v[1]), int(v[2]))
except Exception:
    pass

# If lights.py exports canonical UI hints, prefer those
try:
    PRESET_RGB_HINTS.update(getattr(L, "PRESET_RGB_HINTS", {}) or {})
except Exception:
    pass

def get_preset_rgb(name: str) -> Tuple[int, int, int]:
    if name in PRESET_RGB_HINTS:
        return PRESET_RGB_HINTS[name]

    low = name.lower()
    if "red" in low:
        return (255, 0, 0)
    if "green" in low:
        return (0, 255, 0)
    if "blue" in low:
        return (0, 120, 255)
    if "warm" in low:
        return (255, 200, 150)
    if "cool" in low:
        return (200, 220, 255)
    if "night" in low:
        return (255, 120, 60)
    return (230, 230, 230)

def preset_attr_from_rgb(preset_name: str, colors_enabled: bool) -> int:
    if not colors_enabled:
        return 0

    r, g, b = get_preset_rgb(preset_name)

    if _COLOR_STATE["supports_256"]:
        fg = rgb_to_ansi256(r, g, b)
        return get_dynamic_pair_for_fg(fg)

    basic = closest_basic_color(r, g, b)
    cache = _COLOR_STATE["pair_cache"]
    if basic in cache:
        return curses.color_pair(cache[basic])

    pair_id = _COLOR_STATE["next_pair_id"]
    if _COLOR_STATE["pairs"] and pair_id >= _COLOR_STATE["pairs"]:
        return cpair(7)

    curses.init_pair(pair_id, basic, -1)
    cache[basic] = pair_id
    _COLOR_STATE["next_pair_id"] += 1
    return curses.color_pair(pair_id)

def attr_for_rgb(r: int, g: int, b: int, colors_enabled: bool) -> int:
    if not colors_enabled:
        return 0

    if _COLOR_STATE["supports_256"]:
        fg = rgb_to_ansi256(r, g, b)
        return get_dynamic_pair_for_fg(fg)

    basic = closest_basic_color(r, g, b)
    cache = _COLOR_STATE["pair_cache"]
    if basic in cache:
        return curses.color_pair(cache[basic])

    pair_id = _COLOR_STATE["next_pair_id"]
    if _COLOR_STATE["pairs"] and pair_id >= _COLOR_STATE["pairs"]:
        return cpair(7)

    curses.init_pair(pair_id, basic, -1)
    cache[basic] = pair_id
    _COLOR_STATE["next_pair_id"] += 1
    return curses.color_pair(pair_id)

def build_alt_segments(text: str, rgb1, rgb2, colors_enabled: bool):
    a1 = attr_for_rgb(*rgb1, colors_enabled)
    a2 = attr_for_rgb(*rgb2, colors_enabled)
    segs = []
    flip = False
    for ch in text:
        if ch == " ":
            segs.append((" ", 0))
            continue
        segs.append((ch, a2 if flip else a1))
        flip = not flip
    return segs

# -----------------------------
# Preset list and status
# -----------------------------

def build_preset_list() -> List[str]:
    items = set(L.PRESETS.keys())
    try:
        items |= set(getattr(L, "BACKGROUND_EFFECTS", set()))
    except Exception:
        pass
    items |= set(VIRTUAL_MENU_ACTIONS.keys())
    return sorted(items, key=lambda s: s.lower())

async def fetch_status() -> List[Dict[str, Any]]:
    bulbs = await L.get_bulbs()
    try:
        states = await asyncio.gather(*[b.updateState() for b in bulbs], return_exceptions=True)
        out = []

        for bulb, st in zip(bulbs, states):
            row = {
                "ip": bulb.ip,
                "on": False,
                "bri": None,
                "ct": None,
                "rgb": None,
                "sceneId": None,
                "dimming": None,
                "err": None,
            }

            if isinstance(st, Exception):
                row["err"] = type(st).__name__
                out.append(row)
                continue

            try:
                row["on"] = bool(st.get_state())
                if row["on"]:
                    row["bri"] = st.get_brightness()
                    row["ct"] = st.get_colortemp()
                    row["rgb"] = st.get_rgb()
            except Exception as e:
                row["err"] = type(e).__name__
                out.append(row)
                continue

            # WiZ getPilot: sceneId + dimming, and possibly rgb/temp (often None during scenes)
            try:
                ip = bulb.ip
                now_ts = time.time()

                cached = _PILOT_CACHE.get(ip)
                if cached and (now_ts - cached[0]) < _PILOT_TTL_SEC:
                    resp = cached[1]
                else:
                    # Short timeout so a slow bulb can't freeze the UI
                    resp = L.get_pilot_raw(ip, timeout=_PILOT_TIMEOUT_SEC)
                    if isinstance(resp, dict) and ("result" in resp or "error" in resp):
                        _PILOT_CACHE[ip] = (now_ts, resp)

                res = (resp or {}).get("result") or {}
                row["sceneId"] = res.get("sceneId")
                row["dimming"] = res.get("dimming")

                r = res.get("r")
                g = res.get("g")
                b = res.get("b")
                if isinstance(r, int) and isinstance(g, int) and isinstance(b, int):
                    row["rgb"] = (r, g, b)

                temp = res.get("temp")
                if isinstance(temp, int):
                    row["ct"] = temp
            except Exception:
                pass

            out.append(row)

        return out
    finally:
        await L.close_all(bulbs)

def scene_name_from_id(scene_id: Optional[int]) -> Optional[str]:
    if scene_id is None:
        return None

    # 1) If lights.py already has an explicit mapping, use it.
    for attr in ("SCENE_ID_TO_NAME", "SCENE_NAME_BY_ID", "SCENE_BY_ID", "WIZ_SCENES"):
        m = getattr(L, attr, None)
        if isinstance(m, dict) and scene_id in m:
            try:
                return str(m[scene_id])
            except Exception:
                pass

    # 2) Otherwise, infer from PRESETS that use scene_id.
    names = []
    try:
        presets = getattr(L, "PRESETS", {}) or {}
        for name, preset in presets.items():
            if isinstance(preset, dict):
                sid = preset.get("scene_id")
                if sid == scene_id:
                    names.append(name)
    except Exception:
        pass

    if not names:
        return None

    names = sorted(set(names), key=lambda s: s.lower())
    if len(names) <= 3:
        return "/".join(names)
    return "/".join(names[:3]) + "+"

def fmt_bulb_line(s: Dict[str, Any], width: int) -> str:
    ip = s["ip"]
    label = room_slot_label(ip)
    label_txt = f"{label:<12}"  # room+slot fixed width

    if s.get("err"):
        txt = f"{label_txt} {ip}  ERR  {s['err']}"
        return txt[:width].ljust(width)

    if not s.get("on"):
        txt = f"{label_txt} {ip}  OFF"
        return txt[:width].ljust(width)

    bri = s.get("bri")
    ct = s.get("ct")
    rgb = s.get("rgb")
    scene_id = s.get("sceneId")
    dimming = s.get("dimming")

    rgb_valid = (
        isinstance(rgb, (tuple, list))
        and len(rgb) == 3
        and all(v is not None for v in rgb)
    )

    if rgb_valid:
        txt = f"{label_txt} {ip}  ON   bri={bri} rgb={tuple(rgb)}"
    elif ct is not None:
        txt = f"{label_txt} {ip}  ON   bri={bri} ct={ct}"
    elif scene_id is not None or dimming is not None:
        sname = scene_name_from_id(scene_id if isinstance(scene_id, int) else None)
        if sname:
            txt = f"{label_txt} {ip}  ON   bri={bri} scene={sname} (id={scene_id}) dim={dimming}"
        else:
            txt = f"{label_txt} {ip}  ON   bri={bri} sceneId={scene_id} dim={dimming}"
    else:
        txt = f"{label_txt} {ip}  ON   bri={bri}"

    return txt[:width].ljust(width)

def bulb_line_attr(row: Dict[str, Any], colors_enabled: bool) -> int:
    if not colors_enabled:
        return 0
    if row.get("err"):
        return cpair(4)
    if not row.get("on"):
        return cpair(6)
    return cpair(3)

def _any_running_effect_label() -> Optional[str]:
    """
    Your effect tracking is per-group now:
      effect_running_all, effect_running_kitchen, effect_running_entryway, etc.
    The dashboard process has no active_group set, so we scan them all.
    """
    try:
        groups = []
        try:
            groups = [g for g in getattr(L, "GROUPS", {}).keys() if g != "all"]
        except Exception:
            groups = []

        # check all first, then groups
        to_check: List[Optional[str]] = [None] + groups

        for g in to_check:
            name = L.load_running_effect_name(g)
            if name:
                if g:
                    return f"{name} ({g})"
                return f"{name} (all)"
    except Exception:
        pass
    return None

def get_active_label(last_mode: Optional[str], runner: CmdRunner) -> str:
    # If the dashboard has a running process, show that first
    if runner.running and runner.current_cmd:
        return f"RUNNING: {runner.current_cmd}"

    eff = _any_running_effect_label()
    if eff:
        return f"ACTIVE: {eff}"

    return f"LAST: {last_mode or '-'}"

def _add_segments(stdscr, y: int, x: int, segments, w: int) -> None:
    """
    Write colored segments left-to-right, clipped to screen width.
    segments: List[Tuple[str, int]] where int is curses attr (0 allowed).
    """
    cur_x = x
    max_x = w
    for text, attr in segments:
        if cur_x >= max_x:
            break
        if not text:
            continue

        remaining = max_x - cur_x
        s = text[:remaining]
        try:
            if attr:
                stdscr.addstr(y, cur_x, s, attr)
            else:
                stdscr.addstr(y, cur_x, s)
        except Exception:
            pass
        cur_x += len(s)

def draw_help_line(stdscr, y: int, w: int, colors_enabled: bool) -> None:
    # Removed "cycle" and "alerts" keys since lights.py does not implement them in CLI.
    help_items = [
        ("↑/↓", "select"),
        ("←/→", "dim"),
        ("Enter", "apply"),
        ("TAB", "target"),
        ("o", "off"),
        ("t", "toggle"),
        ("f", "fade (3s)"),
        ("1", "alert"),
        ("2", "alert-police"),
        ("3", "alert-pulse"),
        ("x", "cancel"),
        ("q", "quit"),
    ]

    if not colors_enabled:
        s = "Keys: " + "  ".join([f"[{k}] {lab}" for k, lab in help_items])
        stdscr.addstr(y, 0, s[:w].ljust(w))
        return

    HDR = cpair(2) | curses.A_BOLD
    BR = cpair(5) | curses.A_BOLD
    KEY = cpair(5) | curses.A_BOLD | curses.A_UNDERLINE
    TXT = cpair(7)

    parts = [("Keys: ", HDR)]
    for k, lab in help_items:
        parts.extend([
            ("[", BR),
            (k, KEY),
            ("]", BR),
            (" ", TXT),
            (lab, TXT),
            ("   ", TXT),
        ])

    cur_x = 0
    for text, attr in parts:
        if cur_x >= w:
            break
        s = text[: max(0, w - cur_x)]
        if not s:
            continue
        try:
            stdscr.addstr(y, cur_x, s, attr)
        except Exception:
            pass
        cur_x += len(s)

    if cur_x < w:
        try:
            stdscr.addstr(y, cur_x, (" " * (w - cur_x))[: max(0, w - cur_x)])
        except Exception:
            pass

# -----------------------------
# Draw
# -----------------------------

def draw_screen(stdscr, status_rows, presets, sel_idx, last_mode, msg, colors_enabled, target, runner: CmdRunner):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    title = "[Minotaur] Lights Dashboard"
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    active_label = get_active_label(last_mode, runner)

    header = f"{title}   {active_label}   {now}"
    header = header[:w].ljust(w)

    if colors_enabled:
        stdscr.addstr(0, 0, header, cpair(1))
    else:
        stdscr.addstr(0, 0, header)

    stdscr.hline(1, 0, ord("-"), w)

    left_w = max(35, int(w * 0.55))
    right_w = w - left_w - 1
    if right_w < 20:
        left_w = w
        right_w = 0

    if colors_enabled:
        stdscr.addstr(2, 0, "Bulbs", cpair(2))
    else:
        stdscr.addstr(2, 0, "Bulbs")
    stdscr.hline(3, 0, ord("-"), left_w)

    y = 4
    for row in status_rows:
        if y >= h - 7:
            break
        line = fmt_bulb_line(row, left_w)
        attr = bulb_line_attr(row, colors_enabled)
        if attr:
            stdscr.addstr(y, 0, line, attr)
        else:
            stdscr.addstr(y, 0, line)
        y += 1

    if right_w > 0:
        x0 = left_w + 1
        label = f"Presets (A-Z)  Target={target}"
        if colors_enabled:
            stdscr.addstr(2, x0, label[:right_w], cpair(2))
        else:
            stdscr.addstr(2, x0, label[:right_w])
        stdscr.hline(3, x0, ord("-"), right_w)

        max_lines = h - 10
        if max_lines < 1:
            max_lines = 1

        start = max(0, sel_idx - max_lines // 2)
        end = min(len(presets), start + max_lines)

        y = 4
        for i in range(start, end):
            if y >= h - 7:
                break

            name = presets[i]
            prefix = ">" if i == sel_idx else " "
            line = f"{prefix} {name}"
            line = line[:right_w].ljust(right_w)

            if i == sel_idx:
                stdscr.addstr(y, x0, line, curses.A_REVERSE)
            else:
                if colors_enabled and name in MULTI_COLOR_NAMES:
                    rgb1, rgb2 = MULTI_COLOR_NAMES[name]
                    segs = []
                    segs.append((prefix, 0))
                    segs.append((" ", 0))
                    segs.extend(build_alt_segments(name, rgb1, rgb2, colors_enabled))

                    visible = 2 + len(name)
                    if visible < right_w:
                        segs.append((" " * (right_w - visible), 0))

                    _add_segments(stdscr, y, x0, segs, x0 + right_w)
                else:
                    attr = preset_attr_from_rgb(name, colors_enabled)
                    if attr:
                        stdscr.addstr(y, x0, line, attr)
                    else:
                        stdscr.addstr(y, x0, line)
            y += 1

    stdscr.hline(h - 6, 0, ord("-"), w)

    draw_help_line(stdscr, h - 5, w, colors_enabled)

    run_line = ""
    if runner.running:
        run_line = f"RUNNING: {runner.current_cmd}"
    stdscr.addstr(h - 4, 0, run_line[:w])

    lm = f"Last: {last_mode or '-'}"
    msg_txt = msg or ""
    footer = (lm + "   " + msg_txt)[:w]
    if colors_enabled and msg_txt:
        stdscr.addstr(h - 3, 0, footer, cpair(5))
    else:
        stdscr.addstr(h - 3, 0, footer)

    stdscr.refresh()

# -----------------------------
# Main UI
# -----------------------------

def dashboard(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    colors_enabled = init_colors()

    presets = build_preset_list()
    init_multicolor_from_lights()
    sel_idx = 0
    msg = ""

    last_status: List[Dict[str, Any]] = []
    last_poll = 0.0
    poll_interval_idle = 5.0
    poll_interval_busy = 8.0

    # TAB cycles through combined targets so you can hit new bulbs (entryway) too.
    # Each tuple is (group_prefix_for_cli or None, target_mode)
    targets = [
        (None, "BOTH"),          # all bulbs
        (None, "B1"),
        (None, "B2"),
        ("kitchen", "BOTH"),
        ("entryway", "BOTH"),
        ("kitchen", "B1"),
        ("kitchen", "B2"),
        ("entryway", "B1"),
        ("entryway", "B2"),
    ]
    target_idx = 0

    def with_group(args: List[str]) -> List[str]:
        g, _t = targets[target_idx]
        return ([g] + args) if g else args

    runner = CmdRunner("lights")

    while True:
        new_lines = runner.poll_lines()
        if new_lines:
            msg = new_lines[-1]

        now = time.time()
        poll_interval = poll_interval_busy if runner.running else poll_interval_idle
        if now - last_poll >= poll_interval:
            try:
                last_status = asyncio.run(fetch_status())
            except Exception as e:
                last_status = [{"ip": ip, "on": False, "err": f"{type(e).__name__}"} for ip in L.IPS]
            last_poll = now

        g, tmode = targets[target_idx]
        last_mode = L.load_last_mode(g) if g else L.load_last_mode(None)
        target = f"{(g or 'all').upper()}:{tmode}"
        draw_screen(stdscr, last_status, presets, sel_idx, last_mode, msg, colors_enabled, target, runner)

        ch = stdscr.getch()
        if ch == -1:
            time.sleep(0.05)
            continue

        if ch in (ord("q"), ord("Q")):
            if runner.running:
                runner.cancel()
            return

        if ch in (ord("x"), ord("X")):
            # Cancel any foreground command, then stop any background effect for the current target
            if runner.running:
                runner.cancel()
            runner.poke(with_group(["stop"]))
            msg = "Stopped"
            continue

        if ch in (curses.KEY_UP, ord("k")):
            sel_idx = (sel_idx - 1) % len(presets)
            continue

        if ch in (curses.KEY_DOWN, ord("j")):
            sel_idx = (sel_idx + 1) % len(presets)
            continue

        if ch == curses.KEY_LEFT:
            _g, tmode = targets[target_idx]
            if tmode == "BOTH":
                runner.poke(with_group(["dim", "-10"]))
            elif tmode == "B1":
                runner.poke(with_group(["dim", "b1", "-10"]))
            else:
                runner.poke(with_group(["dim", "b2", "-10"]))
            msg = "Dimming..."
            continue

        if ch == curses.KEY_RIGHT:
            _g, tmode = targets[target_idx]
            if tmode == "BOTH":
                runner.poke(with_group(["dim", "+10"]))
            elif tmode == "B1":
                runner.poke(with_group(["dim", "b1", "+10"]))
            else:
                runner.poke(with_group(["dim", "b2", "+10"]))
            msg = "Brightening..."
            continue

        if ch == 9:  # TAB
            target_idx = (target_idx + 1) % len(targets)
            continue

        if ch in (10, 13, curses.KEY_ENTER):
            mode = presets[sel_idx]

            if mode in VIRTUAL_MENU_ACTIONS:
                runner.start(VIRTUAL_MENU_ACTIONS[mode])
                msg = runner.last_line
                continue

            _g, tmode = targets[target_idx]
            if tmode == "BOTH":
                runner.start(with_group([mode]))
            elif tmode == "B1":
                runner.start(with_group(["b1", mode]))
            else:
                runner.start(with_group(["b2", mode]))

            msg = runner.last_line
            continue

        if ch in (ord("o"), ord("O")):
            runner.start(with_group(["off"]))
            msg = runner.last_line
            continue

        if ch in (ord("t"), ord("T")):
            runner.start(with_group(["toggle"]))
            msg = runner.last_line
            continue

        # Keep 'f' as true fade only for BOTH
        if ch in (ord("f"), ord("F")):
            mode = presets[sel_idx]
            _g, tmode = targets[target_idx]
            if tmode == "BOTH":
                runner.start(with_group(["fade", mode, "3"]))
                msg = runner.last_line
            else:
                msg = "Fade is only for BOTH (use Enter for B1/B2)"
            continue

        if ch == ord("1"):
            runner.start(with_group(["alert", "15"]))
            msg = runner.last_line
            continue

        if ch == ord("2"):
            runner.start(with_group(["alert-police", "15"]))
            msg = runner.last_line
            continue

        if ch == ord("3"):
            runner.start(with_group(["alert-pulse", "15"]))
            msg = runner.last_line
            continue

if __name__ == "__main__":
    curses.wrapper(dashboard)
