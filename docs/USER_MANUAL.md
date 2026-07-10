# User Manual

## First login

After installation, open `http://<your-pi-ip>:8080` and sign in with the
admin account you created during `install.sh`. If you ever forget it, run
`sudo -u pi-nvr /opt/pi-nvr/venv/bin/python3 /opt/pi-nvr/scripts/create_admin.py`
again to add a new admin account.

## Adding a camera

1. Go to **Cameras** > **Add camera**.
2. Enter a name and the RTSP URL, e.g. `rtsp://192.168.1.50:554/stream1`
   (check your camera's manual — most brands document their RTSP path).
3. If your camera exposes a low-resolution "sub stream" (common on ONVIF
   cameras, usually something like `.../stream2`), enter it too — motion
   detection will use it instead of decoding the full-res stream.
4. Choose a **recording mode**: Off, Continuous, Motion-triggered, or
   Scheduled.
5. Save.

Prefer to find cameras automatically? Click **Discover (ONVIF)** to scan
your local network for ONVIF-compatible cameras.

## Live view

The **Live view** page shows a 1/4/9-camera grid. Click **Snap** on any
tile to capture a still image, or **Fullscreen** for a wall-mounted
monitor. Live view decodes video (unlike recording), so it uses more CPU
per open tab — close tabs you're not watching if you're running many
cameras on a Pi 3.

## Playback

Go to **Playback**, pick a camera and month. Days with recordings or
motion events are listed with counts. Select a recording from the list to
play it, download it, or delete it. **Lock** a recording (via the API, or
from the Recordings list) to protect it from automatic cleanup — useful
for evidence you want to keep past your normal retention window.

## Storage

**Storage** lets you point recordings at any drive: click **Browse for
storage location** to see local, USB, SSD, or already-mounted network
paths with free space shown, then **Use this** to register it as a
storage target. Assign cameras to a target from the camera's edit dialog.

Retention is automatic: recordings older than the configured number of
days are deleted, and if any storage target crosses its usage-percent
threshold, the oldest unlocked recordings on that target are pruned until
it's back under the limit. Adjust both under Settings.

## Motion detection

Enable motion detection per camera from the Cameras page. Fine-tune zones
(include specific regions, or exclude ones like a busy street) via the
motion zone API — a visual zone editor is on the roadmap; for now zones
are normalized 0-1 polygon points.

## Notifications

Enable browser notifications in Settings to get a toast in any open
Pi-NVR tab when motion is detected, a camera goes offline, or storage
fills up. Email notifications need SMTP details configured in Settings.

## Remote access via Tailscale

1. Install Tailscale on the Pi: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
2. Install Tailscale on your phone/laptop and sign in to the same account.
3. Visit `http://<tailscale-ip>:8080` from anywhere — live view, PTZ,
   playback, and settings all work exactly as they do on the LAN.

## Backup and restore

Settings > **Export configuration backup** downloads a tarball with your
`config.yaml` and the SQLite database (camera list, users, recording
metadata — not the video files themselves, which are too large for this
to be a full backup solution; back those up separately if needed).
**Restore** uploads that tarball back; restart the service afterward.
