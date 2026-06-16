"""WebSocket routes for real-time dynamic updates."""

import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.geokg.dynamic_update import dynamic_engine

router = APIRouter()


@router.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket):
    """
    WebSocket endpoint for real-time scene updates.
    Clients receive vehicle movements, parking changes, and sensor readings.
    """
    await websocket.accept()
    queue = dynamic_engine.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        dynamic_engine.unsubscribe(queue)
    except Exception:
        dynamic_engine.unsubscribe(queue)
