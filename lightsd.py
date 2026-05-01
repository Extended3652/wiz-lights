#!/usr/bin/env python3

import asyncio
import json
import subprocess
import time
from pathlib import Path

from lights_config import LIGHTS_SCRIPT

# ---------------- CONFIG ----------------

COMMAND_FILE = Path.home() / ".lightsd" / "command.json"
LOG_FILE = Path.home() / ".lightsd" / "lightsd.log"
LIGHTS = str(LIGHTS_SCRIPT)

POLL_INTERVAL = 1.0  # seconds

# ---------------- HELPERS ----------------

def log(msg):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")

# ---------------- ACTIONS ----------------

def run_lights(args):
    p = subprocess.run([LIGHTS, *args], capture_output=True, text=True, check=False)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if out:
        log(out)
    if p.returncode != 0:
        log(f"lights exited {p.returncode}: {' '.join(args)}")

# ---------------- MAIN LOOP ----------------

async def daemon_loop():
    log("lightsd started")

    while True:
        try:
            if COMMAND_FILE.exists():
                data = json.loads(COMMAND_FILE.read_text())
                COMMAND_FILE.unlink()

                action = data.get("action")

                if action == "fade":
                    run_lights(["fade", data["mode"], str(data["seconds"])])
                elif action == "set":
                    run_lights([data["mode"]])
                elif action == "off":
                    run_lights(["off"])

        except Exception as e:
            log(f"ERROR {e}")

        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(daemon_loop())
