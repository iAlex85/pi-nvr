# Pi-NVR Architecture

## Goals

- Run comfortably on a Raspberry Pi 3 (1 GB RAM, quad-core Cortex-A53, no HW AI accel).
- Never transcode video unless the user explicitly asks for it. Recording uses
  `ffmpeg -c copy` to remux the camera's native RTSP stream straight to disk.
- Motion detection runs against a small, decimated, grayscale frame stream —
  never the full-res recording stream — to keep CPU load low.
- No Docker. No Node.js build step. No cloud services. SQLite for state.

## High-level component diagram

```
                     ┌─────────────────────────────┐
                     │         Web Browser          │
                     │  (Dashboard / Live / Config)  │
                     └───────────────┬───────────────┘
                                      │ HTTPS / WSS  (via Tailscale or LAN)
                     ┌───────────────▼───────────────┐
                     │         FastAPI app            │
                     │  app/main.py + app/api/router  │
                     ├─────────────────────────────────┤
                     │ auth │ cameras │ recording │... │  <- routers, one per domain
                     └───┬───────┬─────────┬──────┬────┘
                         │       │         │      │
             ┌───────────▼┐ ┌────▼─────┐ ┌─▼────────────┐ ┌──▼─────────┐
             │ CameraMgr  │ │ Recording│ │ MotionDetector│ │ StorageMgr │
             │ (ONVIF/    │ │ Engine   │ │ (OpenCV,      │ │ (mounts,   │
             │  RTSP)     │ │ (ffmpeg  │ │  downscaled   │ │  retention,│
             │            │ │  subproc)│ │  frames)      │ │  cleanup)  │
             └─────┬──────┘ └────┬─────┘ └───────┬───────┘ └─────┬──────┘
                   │             │               │               │
                   └─────────────┴───────┬───────┴───────────────┘
                                          │
                                 ┌────────▼────────┐
                                 │  SQLite (state)  │
                                 │  + filesystem     │
                                 │  (recordings/,    │
                                 │   snapshots/)      │
                                 └───────────────────┘
```

## Process model

Each enabled camera gets **two long-lived child processes**, managed by the
`RecordingEngine` and `MotionDetector` respectively, plus one shared FastAPI
worker process:

1. **Recording process** — an `ffmpeg` subprocess doing `-c:v copy -c:a copy`
   from the camera's RTSP URL into segmented MP4/MKV files (`-f segment`).
   This process never decodes a frame; it just remuxes packets, which is why
   a Pi 3 can handle several simultaneous camera streams as long as network
   and disk I/O keep up.
2. **Motion-detection process/thread** — a separate low-res pull, either via
   a second lightweight RTSP sub-stream (if the camera exposes one, which is
   preferred and standard on most ONVIF cameras) or via `ffmpeg` scaled
   decode to ~320x180 @ 5fps piped into OpenCV background-subtraction.
   Using the camera's built-in low-res substream avoids decoding the
   high-res stream twice.

The FastAPI process supervises both via `asyncio.create_subprocess_exec` and
restarts them on failure with exponential backoff.

## Directory layout

```
app/
  main.py            FastAPI app factory + startup/shutdown hooks
  config.py           YAML config loader/validator
  database.py          SQLAlchemy engine/session
  models.py             ORM models (User, Camera, Recording, MotionEvent, ...)
  auth/                 login, sessions, password hashing
  cameras/               camera CRUD, ONVIF discovery, PTZ
  recording/               ffmpeg-backed recording engine + scheduler
  motion/                    OpenCV motion detector
  storage/                     mount detection, retention/cleanup
  playback/                      recording browse/stream/download API
  dashboard/                       system stats (CPU/RAM/disk/temp)
  notifications/                     event bus -> email/browser push
  plugins/                            plugin base class + loader
  websocket/                           live event/status fan-out
  api/                                  router aggregation
  static/, templates/                    server-rendered UI (no Node build)
config/                default_config.yaml (copied to /etc/pi-nvr on install)
database/               pi-nvr.db (SQLite, created at first run)
recordings/             default recording root (overridable per-storage-target)
logs/                   application + per-camera logs
scripts/                install.sh, camera_simulator.py, backup/restore
systemd/                pi-nvr.service unit file
tests/                  pytest suite + camera simulator fixtures
```

## Why FastAPI over Flask

FastAPI's ASGI/async model lets a single worker handle many concurrent
WebSocket connections (live MJPEG/status pushes) and short-lived REST calls
without spinning up a thread per connection — important on a Pi 3's 4 cores
and 1 GB RAM budget. Recording/motion workloads live in subprocesses, not in
the web worker, so the web layer stays light.

## Data flow: motion -> recording -> notification

1. `MotionDetector` flags motion on camera N, writes a `MotionEvent` row,
   and publishes an internal event via `app.notifications.manager`.
2. If the camera's recording mode is `motion`, the `RecordingEngine` extends
   the current segment (pre/post buffer) instead of stopping it.
3. `NotificationManager` fans the event out to WebSocket subscribers (for the
   browser notification) and, if configured, SMTP email.

## Performance budget (Pi 3, target: 4 cameras @ 1080p15)

| Component            | Approx. CPU  | Approx. RAM |
|-----------------------|-------------|-------------|
| FastAPI/Uvicorn worker | 3-5%        | ~60 MB      |
| ffmpeg copy per camera  | 2-4%       | ~15 MB      |
| Motion detect per camera | 8-15%     | ~25 MB      |
| SQLite                    | <1%       | ~10 MB      |

Four cameras with motion detection enabled on all of them is roughly
40-70% total CPU on a Pi 3 — leaving headroom, but it's why motion detection
is opt-in per camera and downscaling is not configurable below a sane floor.
