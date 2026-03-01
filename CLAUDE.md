# wiz-lights — Claude Context

## What This Is
A Python CLI + REST API + curses dashboard for controlling WiZ smart bulbs over UDP. Runs on a Raspberry Pi on the local network.

## Hardware
- 4 WiZ bulbs, two rooms:
  - Kitchen: `192.168.86.123`, `192.168.86.124`
  - Entryway: `192.168.86.133`, `192.168.86.134`
- State lives in `/home/pi/.lights_state/` on the Pi

## Key Files
| File | Purpose |
|------|---------|
| `lights.py` | Main CLI — 50+ commands, presets, background effects, group control |
| `lights_dashboard.py` | Curses interactive terminal UI |
| `lights_api.py` | FastAPI HTTP wrapper (home automation integration) |
| `lightsd.py` | Lightweight file-based daemon for reactive control |
| `lights_mqtt.py` | MQTT subscriber → lights commands |

## Protocol
- WiZ bulbs speak UDP JSON on port **38899**
- Use `setPilot` to set state, `getPilot` to read
- Library: `pywizlight` (wraps the UDP protocol with asyncio)

## Dependencies (no requirements.txt — install manually)
```
pywizlight
paho-mqtt
fastapi
uvicorn
```

## Running
```bash
python lights.py <command>          # CLI
python lights_api.py                # HTTP API (localhost:8000)
python lights_dashboard.py          # Curses UI
python lightsd.py                   # File-based daemon
```

## Command Structure
```
lights.py [group] <command> [args]

Groups:  kitchen (k), entryway (e), all (a)  — default: all
```

### Common commands
- `on`, `off`, `toggle`, `status`
- `warm`, `cool`, `bright`, `night`, `movie`  — presets
- `dim <+/- delta>` — relative brightness
- `fade <preset> <seconds>` — smooth transition
- `snap save <name>` / `snap load <name>` — snapshot
- `dashboard` — launch curses UI

### Background effects (long-running processes)
`fireplace_ambient`, `embers`, `bonfire`, `aurora`, `hearth`, `underwater`, `cozy_ambient`, `candle_pair`, `breathe_soft`, `focus_wave`, `dusk_drift`, `storm_distant`, `police_siren`

Effects run as background processes tracked by PID files in `.lights_state/`.
Each group's effect is independent (kitchen can have `embers` while entryway has `aurora`).

## Architecture Notes
- All bulb I/O is **async** (`asyncio`). Use `asyncio.run()` or `await` appropriately.
- Effect brightness is **scaled per group** — don't hardcode absolute brightness values in effects; use the group's current baseline.
- Background effects coordinate across the two bulbs in a room (different timing, color offsets, etc.) for realism.
- Per-group state files track: last preset/scene, running effect PID, effect brightness baseline.

## Git / Deployment
- Dev branch pattern: `claude/<description>-<id>`
- Push: `git push -u origin <branch>`
- Deploy to Pi: `git pull --rebase origin <branch>` on the Pi

## Style Conventions
- Python 3, f-strings, type hints where present
- New effects follow the same pattern as existing ones: async loop, `setPilot` calls, respect group brightness scaling, register in the command dispatch table
- No test suite — manual testing on real hardware
