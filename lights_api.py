from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pywizlight import PilotBuilder, wizlight

import os
import subprocess

from lights_config import GROUPS, IPS as ALL_IPS, LIGHTS_SCRIPT, room_by_ip_lower

LIGHTS = str(LIGHTS_SCRIPT)
app = FastAPI(title="Lights API")

class Cmd(BaseModel):
    cmd: str

def run_lights(args, *, route: str | None = None):
    env = dict(os.environ)
    env["LIGHTS_SOURCE"] = "api"
    if route:
        env["LIGHTS_API_ROUTE"] = route
    p = subprocess.run([LIGHTS, *args], capture_output=True, text=True, env=env)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/status")
def status():
    rc, out = run_lights(["status"], route="/status")
    return {"rc": rc, "out": out}

ROOM_BY_IP = room_by_ip_lower()

@app.get("/status/json")
async def status_json():
    bulbs = [wizlight(ip) for ip in ALL_IPS]
    results = []
    try:
        for bulb in bulbs:
            try:
                state = await bulb.updateState()
                on = state.get_state()
                bri = state.get_brightness()
                ct = state.get_colortemp()
                rgb = state.get_rgb()
                if rgb and all(v is None for v in rgb):
                    rgb = None
                results.append({
                    "ip": bulb.ip,
                    "room": ROOM_BY_IP.get(bulb.ip),
                    "on": bool(on),
                    "brightness": bri,
                    "colortemp": ct,
                    "rgb": rgb,
                })
            except Exception:
                results.append({
                    "ip": bulb.ip,
                    "room": ROOM_BY_IP.get(bulb.ip),
                    "on": None,
                    "error": "unreachable",
                })
    finally:
        for bulb in bulbs:
            await bulb.async_close()
    return {"bulbs": results}

@app.post("/cmd")
def cmd(payload: Cmd):
    args = payload.cmd.strip().split()
    if not args:
        raise HTTPException(400, "empty cmd")
    rc, out = run_lights(args, route="/cmd")
    return {"rc": rc, "out": out}

@app.post("/preset/{name}")
def preset(name: str):
    rc, out = run_lights([name], route=f"/preset/{name}")
    return {"rc": rc, "out": out}

@app.post("/off")
def off():
    rc, out = run_lights(["off"], route="/off")
    return {"rc": rc, "out": out}

@app.post("/cook")
def cook():
    rc, out = run_lights(["cook"], route="/cook")
    return {"rc": rc, "out": out}

VALID_ROOMS = set(GROUPS)

def clamp_brightness(value: int) -> int:
    return max(1, min(255, int(value)))

async def set_group_brightness(room: str, brightness: int):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")

    bri = clamp_brightness(brightness)
    bulbs = [wizlight(ip) for ip in GROUPS[room]]
    results = []
    try:
        for bulb in bulbs:
            try:
                state = await bulb.updateState()
                rgb = state.get_rgb()
                ct = state.get_colortemp()
                rgb_valid = (
                    isinstance(rgb, (tuple, list))
                    and len(rgb) == 3
                    and all(v is not None for v in rgb)
                )
                if rgb_valid:
                    pilot = PilotBuilder(brightness=bri, rgb=tuple(rgb))
                elif ct is not None:
                    pilot = PilotBuilder(brightness=bri, colortemp=int(ct))
                else:
                    pilot = PilotBuilder(brightness=bri, colortemp=2700)
                await bulb.turn_on(pilot)
                results.append({"ip": bulb.ip, "ok": True, "brightness": bri})
            except Exception as exc:
                results.append({"ip": bulb.ip, "ok": False, "error": type(exc).__name__})
    finally:
        for bulb in bulbs:
            await bulb.async_close()
    return {"room": room, "brightness": bri, "bulbs": results}

@app.post("/room/{room}/toggle")
def room_toggle(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "toggle"], route=f"/room/{room}/toggle")
    return {"rc": rc, "out": out}

@app.post("/room/{room}/on")
def room_on(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "on"], route=f"/room/{room}/on")
    return {"rc": rc, "out": out}

@app.post("/room/{room}/off")
def room_off(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "off"], route=f"/room/{room}/off")
    return {"rc": rc, "out": out}

@app.post("/room/{room}/preset/{name}")
def room_preset(room: str, name: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, name], route=f"/room/{room}/preset/{name}")
    return {"rc": rc, "out": out}

@app.post("/room/{room}/brightness/{brightness}")
async def room_brightness(room: str, brightness: int):
    return await set_group_brightness(room, brightness)

@app.post("/fade/{name}/{seconds}")
def fade(name: str, seconds: float):
    rc, out = run_lights(["fade", name, str(seconds)], route=f"/fade/{name}/{seconds}")
    return {"rc": rc, "out": out}

@app.post("/alert/{seconds}")
def alert(seconds: float = 15):
    rc, out = run_lights(["alert", str(seconds)], route=f"/alert/{seconds}")
    return {"rc": rc, "out": out}

@app.post("/alert/police/{seconds}")
def alert_police(seconds: float = 15):
    rc, out = run_lights(["alert-police", str(seconds)], route=f"/alert/police/{seconds}")
    return {"rc": rc, "out": out}

@app.post("/alert/pulse/{seconds}")
def alert_pulse(seconds: float = 15):
    rc, out = run_lights(["alert-pulse", str(seconds)], route=f"/alert/pulse/{seconds}")
    return {"rc": rc, "out": out}

from fastapi.responses import HTMLResponse

UI_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Lights</title>
  <style>
    * { box-sizing: border-box }
    body { font-family: system-ui, sans-serif; background:#111; color:#eee; margin:0; padding:20px }
    main { max-width:760px; margin:0 auto }
    h2 { margin:0 0 16px }
    button { margin:4px; padding:10px 14px; font-size:16px; background:#242424; color:#eee; border:1px solid #444; border-radius:8px }
    button:active { background:#333 }
    .panel { background:#191919; border:1px solid #333; border-radius:10px; padding:14px; margin:14px 0 }
    .rooms { display:grid; grid-template-columns:1fr 1fr; gap:12px }
    .room { background:#202020; border:1px solid #383838; border-radius:10px; padding:12px }
    .all-room { grid-column:1 / -1 }
    .slider-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; font-weight:700 }
    input[type=range] { width:100%; accent-color:#ffd166 }
    input[type=number] { width:90px; padding:8px; background:#202020; color:#eee; border:1px solid #444; border-radius:8px }
    pre { background:#050505; padding:10px; overflow:auto; border-radius:8px; min-height:120px }
    @media (max-width: 620px) {
      body { padding:12px }
      .rooms { grid-template-columns:1fr }
      .all-room { grid-column:auto }
    }
  </style>
</head>
<body>
<main>
  <h2>Lights</h2>

  <div class="panel">
    <button onclick="cmd('warm')">Warm</button>
    <button onclick="cmd('cool')">Cool</button>
    <button onclick="cmd('night')">Night</button>
    <button onclick="cmd('movie')">Movie</button>
    <button onclick="cook()">Cook</button>
    <button onclick="cmd('off')">Off</button>
  </div>

  <section class="panel rooms">
    <div class="room all-room">
      <div class="slider-head"><span>All Rooms</span><span id="allValue">-</span></div>
      <input id="allSlider" type="range" min="1" max="255" value="160" oninput="setBrightness('all', this.value)">
    </div>
    <div class="room">
      <div class="slider-head"><span>Kitchen</span><span id="kitchenValue">-</span></div>
      <input id="kitchenSlider" type="range" min="1" max="255" value="160" oninput="setBrightness('kitchen', this.value)">
    </div>
    <div class="room">
      <div class="slider-head"><span>Entryway</span><span id="entrywayValue">-</span></div>
      <input id="entrywaySlider" type="range" min="1" max="255" value="160" oninput="setBrightness('entryway', this.value)">
    </div>
  </section>

  <section class="panel">
    <h3>Fade</h3>
    <input id="fadeSecs" type="number" value="10" min="1"> seconds
    <button onclick="fade('night')">Fade to Night</button>
  </section>

  <section class="panel">
    <h3>Status</h3>
    <button onclick="status()">Refresh</button>
    <pre id="out"></pre>
  </section>
</main>

<script>
const timers = {}
const roomNames = ['all', 'kitchen', 'entryway']

function cmd(name) {
  fetch('/preset/' + name, {method:'POST'}).then(status)
}
function fade(name) {
  const s = document.getElementById('fadeSecs').value
  fetch(`/fade/${name}/${s}`, {method:'POST'}).then(status)
}
function cook() {
  fetch('/cook', {method:'POST'}).then(status)
}
function valueEl(room) {
  return document.getElementById(room + 'Value')
}
function sliderEl(room) {
  return document.getElementById(room + 'Slider')
}
function setBrightness(room, value) {
  const bri = Number(value)
  valueEl(room).textContent = bri
  if (room === 'all') {
    for (const child of ['kitchen', 'entryway']) {
      sliderEl(child).value = bri
      valueEl(child).textContent = bri
    }
  }
  clearTimeout(timers[room])
  timers[room] = setTimeout(() => {
    fetch(`/room/${room}/brightness/${bri}`, {method:'POST'}).then(status)
  }, 160)
}
function averageBrightness(bulbs) {
  const vals = bulbs
    .filter(b => b.on && Number.isFinite(Number(b.brightness)))
    .map(b => Number(b.brightness))
  if (!vals.length) return null
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
}
function syncSliders(bulbs) {
  const groups = {
    kitchen: bulbs.filter(b => b.room === 'kitchen'),
    entryway: bulbs.filter(b => b.room === 'entryway'),
    all: bulbs,
  }
  for (const room of roomNames) {
    const avg = averageBrightness(groups[room] || [])
    if (avg === null) continue
    sliderEl(room).value = avg
    valueEl(room).textContent = avg
  }
}
function status() {
  fetch('/status/json').then(r=>r.json()).then(j=>{
    if (Array.isArray(j.bulbs)) syncSliders(j.bulbs)
    document.getElementById('out').textContent =
      JSON.stringify(j, null, 2)
  })
}
status()
</script>
</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return UI_HTML
