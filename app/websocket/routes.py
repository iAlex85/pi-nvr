from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependencies import get_current_user_optional

logger = logging.getLogger("pi_nvr.websocket")
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # WebSocket objects expose `.cookies` the same way Request does, so we
    # can reuse the normal session-cookie auth dependency here directly.
    user = await get_current_user_optional(websocket)  # type: ignore[arg-type]
    if user is None:
        await websocket.close(code=4401)
        return

    manager = websocket.app.state.ws_manager
    await manager.connect(websocket)
    try:
        while True:
            # Clients don't need to send anything; we just need to detect
            # disconnects. Ping/pong is handled by the ASGI server.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
