# Pi-NVR

A lightweight, self-hosted CCTV network video recorder built for the
Raspberry Pi 3 (and anything faster: Pi 4/5, Debian, Ubuntu). No Docker,
no Node.js build step, no cloud account, no paid components.

Inspired by MotionEye, Shinobi, and Frigate (minus the AI), but built from
the ground up around one constraint: a Pi 3 has 1 GB of RAM and no hardware
AI accelerator, so the software never decodes video it doesn't have to.

## Features

- RTSP / ONVIF / MJPEG camera support, with ONVIF WS-Discovery
- Continuous, motion-triggered, scheduled, and manual recording — all via
  `ffmpeg -c copy` stream remuxing, not transcoding
- OpenCV motion detection on a downscaled substream, with include/exclude
  zones, sensitivity, and object size filtering
- ONVIF PTZ control (pan/tilt/zoom, presets, home position)
- Live view (1/4/9-camera grid, fullscreen, snapshots)
- Calendar-based playback browser with seek, download, lock, delete
- Storage manager: pick any local/USB/SSD/network mount from the UI,
  automatic retention by age and by disk usage percentage
- Dashboard: CPU/RAM/disk/temperature, per-camera status, motion counts
- Browser + email notifications (motion, camera offline, low disk, etc.)
- Works cleanly over [Tailscale](https://tailscale.com) — no port forwarding,
  no cloud relay, just `http://<tailscale-ip>:<port>`
- SQLite, systemd, no external services

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit
together and why they're built this way.

## Requirements

- Raspberry Pi 3 or newer (or any Debian/Ubuntu x86_64/arm64 machine)
- Raspberry Pi OS (Bookworm or newer) / Debian 12+ / Ubuntu 22.04+
- Cameras that speak RTSP (virtually all IP cameras, including most
  "generic Chinese WiFi cameras") and/or ONVIF

## Install

```bash
git clone https://github.com/your-org/pi-nvr.git
cd pi-nvr
sudo ./install.sh
```

The installer installs `ffmpeg`, creates a Python virtualenv, sets up
`/opt/pi-nvr`, generates a random session/DB-encryption secret, installs
and starts a `pi-nvr` systemd service, and walks you through creating the
first admin account (there is no default password shipped).

When it finishes, it prints the URL to open — typically
`http://<pi-ip>:8080`.

See [`docs/INSTALL.md`](docs/INSTALL.md) for manual/dev installation and
troubleshooting.

## Remote access

Install [Tailscale](https://tailscale.com/download) on the Pi and on your
phone/laptop, then visit `http://<tailscale-ip>:8080` from anywhere — no
port forwarding, no additional configuration on Pi-NVR's side.

## Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 scripts/create_admin.py
uvicorn app.main:app --reload
pytest
```

See [`docs/DEVELOPER.md`](docs/DEVELOPER.md) for the project layout and
contribution workflow, and [`docs/API.md`](docs/API.md) for the REST API
surface.

## License

MIT — see [`LICENSE`](LICENSE).
