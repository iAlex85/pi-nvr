# Developer Guide

## Project layout

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full breakdown. In short,
each domain (cameras, recording, motion, storage, playback, dashboard,
auth) is a self-contained package under `app/` with its own `routes.py`;
`app/api/router.py` just aggregates them.

## Setting up a dev environment

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export PI_NVR_CONFIG=$(pwd)/config/default_config.yaml
python3 scripts/create_admin.py
uvicorn app.main:app --reload
```

You don't need real cameras to develop against: `scripts/camera_simulator.py`
generates a synthetic looping test clip. For real RTSP semantics (seeking,
reconnects), run it alongside a local [MediaMTX](https://github.com/bluenviron/mediamtx)
instance; for quick HTTP-based checks it can also just serve the clip over
plain HTTP.

## Running tests

```bash
pytest
```

Each test gets an isolated temp directory with its own `config.yaml` and
SQLite DB (see `tests/conftest.py`), so tests never touch real data and can
run in any order.

## Code style

- PEP 8, type hints on public function signatures.
- Prefer explicit dotted-path config access (`cfg.get("motion.sensitivity")`)
  over hardcoding a value — if it's a number a user might want to change,
  it belongs in `config/default_config.yaml`.
- Keep `routes.py` files thin: validation + calling into a manager/engine
  class, not business logic. Business logic lives in the sibling module
  (`manager.py`, `engine.py`, `detector.py`, etc.) so it's testable without
  spinning up FastAPI.
- Long-running work (ffmpeg subprocesses, OpenCV frame loops) always goes
  through `asyncio.create_subprocess_exec` / `run_in_executor`, never
  blocking the event loop directly.

## Adding a new domain module

1. `mkdir app/newthing && touch app/newthing/__init__.py`
2. Add `app/newthing/routes.py` with an `APIRouter`.
3. Register it in `app/api/router.py`.
4. If it needs a background task, follow the pattern in
   `app/motion/detector.py`'s `MotionSupervisor` (a `start()`/`stop()`
   pair on `app.state`, wired up in `app/main.py`'s `lifespan`).
5. Add config keys to `config/default_config.yaml` — never hardcode
   tunables in Python.
6. Add tests under `tests/`.

## Plugin architecture

`app/plugins/base.py` defines the plugin interface. Plugins listed under
`plugins.enabled` in config.yaml are loaded at startup. This is the
intended extension point for future work (AI object detection, license
plate recognition, Home Assistant/MQTT/Telegram/Discord integrations,
cloud backup) without touching core recording/motion code.

## Release process

1. Update `CHANGELOG.md`.
2. Tag: `git tag vX.Y.Z && git push --tags`.
3. GitHub Actions CI (`.github/workflows/ci.yml`) runs the test suite on
   every push/PR; no separate release build step is needed since this
   ships as source, not a packaged binary.
