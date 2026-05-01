# Repository Guidelines

## Project Structure & Module Organization

This repository is a small Python control surface for WiZ lights. Core light behavior, presets, groups, and CLI command handling live in `lights.py`. API endpoints are in `lights_api.py`, the dashboard is in `lights_dashboard.py`, MQTT bridging is in `lights_mqtt.py`, and the polling daemon is in `lightsd.py`. Helper scripts such as `all_on.py`, `all_off.py`, `discover.py`, `control.py`, and `test_on.py` are local utilities and smoke checks. There is no package directory or formal test tree yet.

## Build, Test, and Development Commands

- `./lights.py status` shows current bulb state.
- `./lights.py warm`, `./lights.py off`, or `./lights.py kitchen toggle` exercise common CLI paths.
- `uvicorn lights_api:app --host 0.0.0.0 --port 8000` runs the FastAPI service locally.
- `./lights_mqtt.py` starts the MQTT command bridge; configure it with `MQTT_HOST`, `MQTT_PORT`, and `MQTT_TOPIC`.
- `./lightsd.py` starts the file-backed daemon loop.
- `python3 -m py_compile *.py` is the quickest syntax check before committing.

Commands talk to real bulbs at fixed LAN IPs, so prefer targeted checks when away from the live network.

## Coding Style & Naming Conventions

Use Python 3 and keep the existing straightforward script style. Follow PEP 8 with 4-space indentation, `snake_case` functions and variables, uppercase constants such as `IPS`, `GROUPS`, and `STATE_FILE`, and short helpers for repeated behavior. Keep exposed command names lowercase and hyphenated, for example `alert-pulse`.

## Testing Guidelines

There is no formal test framework or coverage target. Validate changes with `python3 -m py_compile *.py` and at least one focused smoke command, such as `./lights.py status` or `./lights.py kitchen off`, when bulbs are reachable. For behavior that should not hit hardware, add pure helpers so future unit tests can cover them without network access.

## Commit & Pull Request Guidelines

Recent history uses short imperative commit subjects, for example `Fix room toggle buttons and add room-specific API endpoints` and `Add bonfire and aurora effects`. Keep commits focused and user-visible. Follow `AGENT_INSTRUCTIONS.md`: work on feature branches, do not commit directly to `main`, open PRs, and wait for approval before merging. PR descriptions should include commands run, affected scripts or endpoints, and hardware-dependent testing limitations.

## Security & Configuration Tips

Do not commit secrets, local broker credentials, or private network changes unrelated to the task. State files are written under `/home/pi/.lights_state`, and several scripts call `/home/pi/bin/lights`; update these paths deliberately if deployment layout changes.
