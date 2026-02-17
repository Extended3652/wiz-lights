#!/usr/bin/env python3

import asyncio
import json
import time
from pathlib import Path
from pywizlight import wizlight, PilotBuilder

# ---------------- CONFIG ----------------

IPS = [
    "192.168.86.123",
    "192.168.86.124",
]

COMMAND_FILE = Path.home() / ".lightsd" / "command.json"
LOG_FILE = Path.home() / ".lightsd" / "lightsd.log"

PRESETS = {
    "warm":   PilotBuilder(brightness=180, colortemp=2700),
    "cool":   PilotBuilder(brightness=220, colortemp=5000),
    "night":  PilotBuilder(brightness=30,  colortemp=2200),
    "sunset": PilotBuilder(brightness=120, rgb=(255,120,40)),
}

POLL_INTERVAL = 1.0  # seconds

# ---------------- HELPERS ----------------

def log(msg):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")

async def get_bulbs():
    return [wizlight(ip) for ip in IPS]

async def close_all(bulbs):
    for b in bulbs:
        await b.async_close()

# ---------------- ACTIONS ----------------

async def fade_to(mode, seconds):
    bulbs = await get_bulbs()
    target = PRESETS[mode]

    steps = max(int(seconds * 10), 1)
    delay = seconds / steps
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    try:
        states = await asyncio.gather(
            *[b.updateState() for b in bulbs]
        )
        start_bris = [s.get_brightness() or 0 for s in states]

        for i in range(steps):
            level = (i + 1) / steps
            tasks = []

            for bulb, start_bri in zip(bulbs, start_bris):
                bri = int(start_bri + (target.brightness - start_bri) * level)
                tasks.append(
                    bulb.turn_on(PilotBuilder(brightness=bri))
                )

            await asyncio.gather(*tasks)
            await asyncio.sleep(max(0, start_time + (i + 1) * delay - loop.time()))

        await asyncio.gather(
            *[b.turn_on(target) for b in bulbs]
        )

        log(f"FADE completed -> {mode}")

    finally:
        await close_all(bulbs)

async def apply_preset(mode):
    bulbs = await get_bulbs()
    try:
        for b in bulbs:
            await b.turn_on(PRESETS[mode])
        log(f"SET {mode}")
    finally:
        await close_all(bulbs)

async def turn_off():
    bulbs = await get_bulbs()
    try:
        for b in bulbs:
            await b.turn_off()
        log("OFF")
    finally:
        await close_all(bulbs)

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
                    await fade_to(data["mode"], data["seconds"])
                elif action == "set":
                    await apply_preset(data["mode"])
                elif action == "off":
                    await turn_off()

        except Exception as e:
            log(f"ERROR {e}")

        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(daemon_loop())
