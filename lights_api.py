from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pywizlight import wizlight

import subprocess

LIGHTS = "/home/pi/bin/lights"
app = FastAPI(title="Lights API")

class Cmd(BaseModel):
    cmd: str

def run_lights(args):
    p = subprocess.run([LIGHTS, *args], capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/status")
def status():
    rc, out = run_lights(["status"])
    return {"rc": rc, "out": out}

ALL_IPS = [
    "192.168.86.123",  # kitchen 1
    "192.168.86.124",  # kitchen 2
    "192.168.86.133",  # entryway 1
    "192.168.86.134",  # entryway 2
]

ROOM_BY_IP = {
    "192.168.86.123": "kitchen",
    "192.168.86.124": "kitchen",
    "192.168.86.133": "entryway",
    "192.168.86.134": "entryway",
}

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
    rc, out = run_lights(args)
    return {"rc": rc, "out": out}

@app.post("/preset/{name}")
def preset(name: str):
    rc, out = run_lights([name])
    return {"rc": rc, "out": out}

@app.post("/off")
def off():
    rc, out = run_lights(["off"])
    return {"rc": rc, "out": out}

VALID_ROOMS = {"kitchen", "entryway", "all"}

@app.post("/room/{room}/toggle")
def room_toggle(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "toggle"])
    return {"rc": rc, "out": out}

@app.post("/room/{room}/on")
def room_on(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "on"])
    return {"rc": rc, "out": out}

@app.post("/room/{room}/off")
def room_off(room: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, "off"])
    return {"rc": rc, "out": out}

@app.post("/room/{room}/preset/{name}")
def room_preset(room: str, name: str):
    if room not in VALID_ROOMS:
        raise HTTPException(404, f"Unknown room: {room}")
    rc, out = run_lights([room, name])
    return {"rc": rc, "out": out}

@app.post("/fade/{name}/{seconds}")
def fade(name: str, seconds: float):
    rc, out = run_lights(["fade", name, str(seconds)])
    return {"rc": rc, "out": out}

@app.post("/alert/{seconds}")
def alert(seconds: float = 15):
    rc, out = run_lights(["alert", str(seconds)])
    return {"rc": rc, "out": out}

@app.post("/alert/police/{seconds}")
def alert_police(seconds: float = 15):
    rc, out = run_lights(["alert-police", str(seconds)])
    return {"rc": rc, "out": out}

@app.post("/alert/pulse/{seconds}")
def alert_pulse(seconds: float = 15):
    rc, out = run_lights(["alert-pulse", str(seconds)])
    return {"rc": rc, "out": out}

from fastapi.responses import HTMLResponse

UI_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Lights</title>
  <style>
    body { font-family: sans-serif; background:#111; color:#eee; padding:20px }
    button { margin:4px; padding:10px 14px; font-size:16px }
    input { width:120px }
    pre { background:#000; padding:10px }
  </style>
</head>
<body>
  <h2>Lights</h2>

  <div>
    <button onclick="cmd('warm')">Warm</button>
    <button onclick="cmd('cool')">Cool</button>
    <button onclick="cmd('night')">Night</button>
    <button onclick="cmd('movie')">Movie</button>
    <button onclick="cmd('off')">Off</button>
  </div>

  <h3>Fade</h3>
  <input id="fadeSecs" type="number" value="10" min="1"> seconds
  <button onclick="fade('night')">Fade to Night</button>

  <h3>Status</h3>
  <button onclick="status()">Refresh</button>
  <pre id="out"></pre>

<script>
function cmd(name) {
  fetch('/preset/' + name, {method:'POST'}).then(status)
}
function fade(name) {
  const s = document.getElementById('fadeSecs').value
  fetch(`/fade/${name}/${s}`, {method:'POST'}).then(status)
}
function status() {
  fetch('/status/json').then(r=>r.json()).then(j=>{
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
