# API Reference

All endpoints are prefixed with `/api` and (except `/api/auth/login`)
require a valid session cookie, set by logging in. FastAPI also serves
interactive docs at `/docs` (Swagger UI) and `/redoc` when the app is
running, generated from the same route definitions as this document —
treat this file as the human-readable tour and `/docs` as the source of
truth for exact request/response schemas.

## Auth (`/api/auth`)

| Method | Path | Description |
|---|---|---|
| POST | `/login` | `{username, password, remember_me}` -> sets session cookie |
| POST | `/logout` | Clears the session cookie |
| GET | `/me` | Current user info |
| POST | `/change-password` | `{current_password, new_password}` |

## Cameras (`/api/cameras`)

| Method | Path | Description |
|---|---|---|
| GET | `` | List cameras |
| POST | `` | Create camera (admin) |
| GET | `/{id}` | Get one camera |
| PUT | `/{id}` | Update camera (admin) |
| DELETE | `/{id}` | Delete camera (admin) |
| GET | `/{id}/status` | Live connection status |
| GET | `/status/all` | Status for every camera, keyed by id |
| POST | `/discover` | ONVIF WS-Discovery scan of the LAN (admin) |
| POST | `/scan-network` | Fallback LAN sweep for hosts with RTSP/ONVIF/HTTP ports open, for cameras that don't answer ONVIF discovery (admin) |
| GET | `/{id}/mjpeg` | MJPEG live-view stream (multipart/x-mixed-replace) |
| POST | `/{id}/ptz/detect` | Probe + persist whether the camera supports PTZ |
| POST | `/{id}/ptz/move` | `{direction, speed}` — direction one of up/down/left/right/zoom_in/zoom_out |
| POST | `/{id}/ptz/stop` | Stop any in-progress PTZ motion |
| POST | `/{id}/ptz/home` | Go to home position |
| GET/POST | `/{id}/ptz/presets` | List / save PTZ presets |
| POST | `/{id}/ptz/presets/{preset_id}/goto` | Move to a saved preset |
| DELETE | `/{id}/ptz/presets/{preset_id}` | Delete a preset |

## Recording (`/api/recordings`)

| Method | Path | Description |
|---|---|---|
| POST | `/{camera_id}/start` | Manually start continuous recording (admin) |
| POST | `/{camera_id}/stop` | Manually stop recording (admin) |
| GET | `/{camera_id}/active` | Whether the camera is currently recording |
| GET | `` | List recordings, optional `?camera_id=` |
| POST | `/{id}/lock` / `/unlock` | Exempt/include a recording from retention cleanup |
| GET/PUT | `/{camera_id}/schedule` | Read/write scheduled-recording windows |

## Motion (`/api/motion`)

| Method | Path | Description |
|---|---|---|
| GET | `/events` | List motion events, optional `?camera_id=&limit=` |
| POST | `/{camera_id}/enable` / `/disable` | Toggle motion detection (admin) |
| GET/POST | `/{camera_id}/zones` | List / create include/exclude zones |
| DELETE | `/zones/{zone_id}` | Delete a zone |

## Storage (`/api/storage`)

| Method | Path | Description |
|---|---|---|
| GET/POST | `/targets` | List / create storage targets |
| DELETE | `/targets/{id}` | Delete a target (must have no cameras assigned) |
| GET | `/browse` | List available local/USB/network mount points with usage |
| GET | `/targets/{id}/usage` | Disk usage for one target |
| POST | `/cleanup-now` | Run the retention/cleanup pass immediately (admin) |

## Playback (`/api/playback`)

| Method | Path | Description |
|---|---|---|
| GET | `/calendar?camera_id=&year=&month=` | Per-day recording/motion counts |
| GET | `/stream/{recording_id}` | Range-seekable video stream |
| GET | `/download/{recording_id}` | Force-download the file |
| DELETE | `/{recording_id}` | Delete a recording (blocked if locked) |
| POST | `/snapshot/{camera_id}` | Capture a snapshot right now |
| GET | `/snapshot/{camera_id}/latest` | Most recent snapshot image |

## System / Dashboard (`/api/system`)

| Method | Path | Description |
|---|---|---|
| GET | `/stats` | CPU/RAM/disk/temp + camera/recording/motion summary |
| GET | `/logs` | Queryable event log, `?category=&level=&limit=` |
| GET | `/logs/download` | Download the raw log file (admin) |
| GET/PUT | `/settings` | Read/write the full config (admin) |
| POST | `/restart-service` | `systemctl restart pi-nvr` (admin) |
| POST | `/restart-device` / `/shutdown-device` | Reboot / power off the Pi (admin) |
| GET | `/backup` | Download a config+DB backup tarball (admin) |
| POST | `/restore` | Upload and restore a backup tarball (admin) |

## WebSocket (`/api/ws`)

Authenticated (session cookie) WebSocket for live push: motion events,
camera status changes, notifications. Messages are JSON:
`{"type": "motion", "data": {"camera_id": 1, "camera_name": "Front Door", "score": 4.2}}`.
