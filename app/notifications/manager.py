"""
NotificationManager is the single place events get published from
(motion, camera offline, storage full, recording stopped, low disk space).
It fans each event out to:
  - connected WebSocket clients (for in-browser notifications), always
  - email via SMTP, if configured and the event type is enabled

Keeping this as one narrow-interface class means callers (MotionSupervisor,
StorageManager, CameraManager) never need to know *how* a notification is
delivered -- they just call `publish(event_type, data)`.
"""
from __future__ import annotations

import logging

from app.config import Config

logger = logging.getLogger("pi_nvr.notifications")

EMAIL_SUBJECTS = {
    "motion": "Motion detected",
    "camera_offline": "Camera offline",
    "storage_full": "Storage full",
    "recording_stopped": "Recording stopped",
    "low_disk_space": "Low disk space",
}


class NotificationManager:
    def __init__(self, cfg: Config, ws_manager):
        self.cfg = cfg
        self.ws_manager = ws_manager

    async def publish(self, event_type: str, data: dict) -> None:
        logger.info("Event: %s %s", event_type, data)

        if self.cfg.get("notifications.browser_enabled", True):
            await self.ws_manager.broadcast(event_type, data)

        if self.cfg.get("notifications.email.enabled", False):
            await self._send_email(event_type, data)

    async def _send_email(self, event_type: str, data: dict) -> None:
        import aiosmtplib
        from email.message import EmailMessage

        to_addresses = self.cfg.get("notifications.email.to_addresses", [])
        from_address = self.cfg.get("notifications.email.from_address", "")
        if not to_addresses or not from_address:
            return

        subject = EMAIL_SUBJECTS.get(event_type, "Pi-NVR notification")
        body_lines = [f"{k}: {v}" for k, v in data.items()]
        message = EmailMessage()
        message["From"] = from_address
        message["To"] = ", ".join(to_addresses)
        message["Subject"] = f"[Pi-NVR] {subject}"
        message.set_content("\n".join(body_lines))

        import os

        try:
            await aiosmtplib.send(
                message,
                hostname=self.cfg.get("notifications.email.smtp_host", ""),
                port=self.cfg.get("notifications.email.smtp_port", 587),
                username=self.cfg.get("notifications.email.smtp_user", ""),
                password=os.environ.get(
                    self.cfg.get("notifications.email.smtp_password_env", "PI_NVR_SMTP_PASSWORD"),
                    "",
                ),
                start_tls=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send email notification for event '%s'", event_type)
