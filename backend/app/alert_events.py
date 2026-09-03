"""In-process pub/sub for immediate alert delivery over /ws/alerts (see
main.py). alerts_db.log_alert()/resolve_alert() remain the durable write —
this is the fast path that pushes the SAME data to every already-connected
client the instant something changes, instead of a client having to wait
for its next poll.

The tricky part: alerts are created from PipelineManager's receiver
THREAD (pipeline.py, plain threading — not asyncio) and from FastAPI's
sync `def` endpoints (run in Starlette's threadpool, also not the asyncio
loop), while sending over a WebSocket is only legal from FastAPI/uvicorn's
own asyncio event loop. asyncio.run_coroutine_threadsafe is the standard
bridge for exactly this: schedule a coroutine onto a loop from any other
thread, without blocking the caller.
"""
import asyncio
import logging
import time

from . import alerts_db

logger = logging.getLogger("dashboard.alert_events")

_clients: set = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once, from within a request/websocket handler (so it's
    guaranteed to run ON the loop) — see main.py's startup hook. Rebinding
    on every call is harmless (there's only ever one loop for this
    single-worker uvicorn process), so no guard against calling it more
    than once."""
    global _loop
    _loop = loop


def register(websocket) -> None:
    _clients.add(websocket)


def unregister(websocket) -> None:
    _clients.discard(websocket)


def client_count() -> int:
    return len(_clients)


def broadcast() -> None:
    """Thread-safe: call this immediately after alerts_db.log_alert(),
    alerts_db.upgrade_unknown_zone_alert(), or alerts_db.resolve_alert()
    durably writes — pushes the current unresolved-alert list to every
    connected client right now, not on the next timer tick (there is no
    timer). A no-op if nobody's connected yet or the loop hasn't been
    bound (e.g. the very first alert ever, before any client has
    connected)."""
    ms = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    if _loop is None or not _clients:
        logger.info("[%s] broadcast skipped (no loop bound or no clients connected)", ms)
        return
    logger.info("[%s] WebSocket event emitted to %d client(s)", ms, len(_clients))
    payload = alerts_db.list_alerts_with_camera_names(resolved=False)
    for ws in list(_clients):
        asyncio.run_coroutine_threadsafe(_safe_send(ws, payload), _loop)


async def _safe_send(websocket, payload: list[dict]) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:
        # Connection is closing/closed — the websocket handler's own
        # except block (WebSocketDisconnect etc.) unregisters it; nothing
        # further to do here, and this must never raise into the loop.
        pass
