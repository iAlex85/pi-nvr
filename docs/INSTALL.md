# Installation Guide

## Automated install (recommended)

```bash
git clone https://github.com/your-org/pi-nvr.git
cd pi-nvr
sudo ./install.sh
```

What it does, in order:

1. Installs `ffmpeg`, `python3-venv`, `sqlite3` via `apt`.
2. Creates a dedicated, unprivileged `pi-nvr` system user.
3. Copies the application to `/opt/pi-nvr`.
4. Creates a Python virtualenv at `/opt/pi-nvr/venv` and installs
   `requirements.txt` into it.
5. Writes `/etc/pi-nvr/config.yaml` (from `config/default_config.yaml`,
   with paths rewritten to the installed locations). If a config already
   exists there, it's left untouched — re-running `install.sh` is safe.
6. Generates random session-signing and DB-encryption secrets into
   `/etc/pi-nvr/environment` (mode 600).
7. Installs and enables `systemd/pi-nvr.service`.
8. Prompts you to create the first admin account (there is no default
   username/password).
9. Starts the service and prints the URL to open.

## Manual installation (any Debian/Ubuntu system, incl. dev machines)

```bash
sudo apt install python3-venv ffmpeg sqlite3
git clone https://github.com/your-org/pi-nvr.git
cd pi-nvr
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export PI_NVR_CONFIG=$(pwd)/config/default_config.yaml
python3 scripts/create_admin.py

uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Visit `http://localhost:8080`.

## Updating

```bash
cd /opt/pi-nvr
sudo git pull
sudo -u pi-nvr ./venv/bin/pip install -r requirements.txt
sudo systemctl restart pi-nvr
```

(If you installed via `install.sh` from a different checkout location,
`git pull` inside `/opt/pi-nvr` works because the installer copies the
whole repo there, `.git` included when `rsync` is available.)

## Uninstalling

```bash
sudo systemctl disable --now pi-nvr
sudo rm -rf /opt/pi-nvr /etc/pi-nvr /etc/systemd/system/pi-nvr.service
sudo userdel pi-nvr
sudo systemctl daemon-reload
```

This does **not** touch any recordings you moved to external storage
outside `/opt/pi-nvr/recordings` — those are just files on your drive.

## Troubleshooting

**Service won't start / `systemctl status pi-nvr` shows a crash loop**
Check `journalctl -u pi-nvr -e` for the traceback. The most common cause
is a camera RTSP URL that's unreachable at startup; Pi-NVR should retry
with backoff rather than crash, so a persistent crash usually points at a
config or dependency problem instead.

**Camera shows "offline" but VLC can play the RTSP URL fine**
Confirm the URL works with `-rtsp_transport tcp` specifically (some
cameras only support UDP, or vice versa):
```bash
ffprobe -rtsp_transport tcp rtsp://user:pass@camera-ip:554/stream1
```

**Live view works once, then goes blank, or flickers offline/online**
Many budget/consumer IP cameras only accept **one RTSP connection at a
time** -- a second connection attempt doesn't queue, it knocks the first
one loose or fails outright. If you have the camera's phone app open
*and* Pi-NVR's live view open *and* recording active all at once, they're
competing for that single slot. Close the phone app while using Pi-NVR,
and avoid leaving live view open in a browser tab you're not actually
watching. Pi-NVR's own background health-check is designed to skip
re-probing a camera that's already confirmed online via an active
recording connection, specifically to avoid contributing to this
contention -- but it can't do anything about other apps also connected to
the same camera.

**High CPU usage**
Turn off timestamp/name overlay (Settings > Recording) if enabled — that's
the one feature that forces a decode+encode instead of a stream copy.
Motion detection is the next biggest cost; lower `motion.sample_fps` or
disable it on cameras you don't need it for.

**"Permission denied" writing to a USB drive**
Storage targets need to be writable by the `pi-nvr` service user. If you
mounted the drive manually, `chown -R pi-nvr:pi-nvr /path/to/mount` (or
mount with `uid=`/`gid=` options matching the `pi-nvr` user for
FAT/exFAT drives, which don't have Unix permissions at all).
