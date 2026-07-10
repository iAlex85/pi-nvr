"""Tracks active WebSocket connections and fans out JSON events to all of
them (camera status changes, motion events, recording state, notifications).
Deliberately simple -- a Pi 3 NVR has a handful of concurrent browser tabs,
not thousands of clients, so a plain list is fine; no need for pub/sub
infrastructure."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger("pi_nvr.websocket")


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.debug("WebSocket connected (%d total)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.debug("WebSocket disconnected (%d total)", len(self._connections))

    async def broadcast(self, event_type: str, data: dict) -> None:
        payload = json.dumps({"type": event_type, "data": data})
        dead: list[WebSocket] = []
        async with self._lock:
            connections = list(self._connections)
        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)
