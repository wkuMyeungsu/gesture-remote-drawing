"""stream (8002): 휴대폰 카메라 페이지를 주고, 프레임을 받아 vision으로 넘긴다.

연결 (docs/architecture.html, docs/contracts/README.md):
  ①③ 휴대폰 ↔ stream (security 경유): GET /m/ 카메라 페이지, WebSocket /ws/stream
  ④  stream → vision: POST http://vision:8003/frames
  ⑧  stream → web:    POST http://web:8000/events
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse

from .config import load_settings
from .relay import Relay, Session, now_ms

log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.client = httpx.AsyncClient()
    app.state.relay = Relay(settings, app.state.client)
    log.info("stream 시작: vision=%s, events=%s, 시간 제한 %dms%s", settings.vision_url or "(보내지 않음)",
             settings.events_url or "(알리지 않음)", settings.timeout_ms,
             f", 개발용 세션 허용({settings.dev_sid})" if settings.dev_sid else "")
    cleaner = asyncio.create_task(_clean_sessions(app))
    yield
    cleaner.cancel()
    await app.state.relay.notify_all_disconnected()
    await app.state.client.aclose()


async def _clean_sessions(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(60)
        removed = app.state.relay.prune(app.state.settings.session_ttl)
        if removed:
            log.info("끝난 세션 %d개 정리", len(removed))


app = FastAPI(title="stream", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    s = request.app.state
    return {
        "service": "stream",
        "status": "ok",
        "vision_url": s.settings.vision_url or None,
        "phones": sum(1 for x in s.relay.sessions.values() if x.phone is not None),
    }


@app.get("/sessions")
async def sessions(request: Request):
    """개발·디버그용: 세션별 처리 현황."""
    return [x.stats() for x in request.app.state.relay.sessions.values()]


@app.get("/m")
async def camera_redirect():
    return RedirectResponse("/m/")


@app.get("/m/")
async def camera_page():
    """휴대폰 카메라 페이지. QR 주소(/m/?token=…)로 들어오면 security가 토큰을 확인한 뒤 여기로 넘긴다."""
    return FileResponse(STATIC / "camera.html", headers={"Cache-Control": "no-store"})


def _session_id(ws: WebSocket) -> str | None:
    """세션은 security(nginx)가 붙인 X-Session-Id만 믿는다. 개발용 설정이 있을 때만 ?sid=를 받는다."""
    sid = ws.headers.get("x-session-id", "").strip()
    if sid:
        return sid
    dev_sid = ws.app.state.settings.dev_sid
    if dev_sid:
        return ws.query_params.get("sid", "").strip() or dev_sid
    return None


def _parse_frame(text: str, max_bytes: int) -> tuple[int | None, bytes | None]:
    """{"frame_id": 42, "jpeg": "<base64>"} → (frame_id, JPEG 바이트). 틀리면 (frame_id 또는 None, None)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(msg, dict):
        return None, None
    frame_id = msg.get("frame_id")
    frame_id = frame_id if isinstance(frame_id, int) and not isinstance(frame_id, bool) else None
    data = msg.get("jpeg")
    if frame_id is None or not isinstance(data, str):
        return frame_id, None
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    if len(data) > max_bytes * 4 // 3 + 4:
        return frame_id, None
    try:
        jpeg = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return frame_id, None
    if jpeg[:2] != b"\xff\xd8":
        return frame_id, None
    return frame_id, jpeg


async def _ack(session: Session, ws: WebSocket, frame_id: int | None, reason: str | None) -> None:
    payload = {"type": "ack", "frame_id": frame_id, "dropped": reason is not None}
    if reason:
        payload["reason"] = reason
    try:
        await ws.send_json(payload)
    except Exception:
        pass  # 휴대폰이 이미 끊김


async def _process(relay: Relay, session: Session, ws: WebSocket, frame_id: int, jpeg: bytes, ts: int) -> None:
    try:
        reason = await relay.forward(session, frame_id, jpeg, ts)
    finally:
        session.busy = False
    await _ack(session, ws, frame_id, reason)


@app.websocket("/ws/stream")
async def stream_socket(ws: WebSocket):
    state = ws.app.state
    settings, relay = state.settings, state.relay
    await ws.accept()
    sid = _session_id(ws)
    if sid is None:
        await ws.close(code=4401, reason="no session")
        return

    session = relay.session(sid)
    previous, session.phone = session.phone, ws
    if previous is not None:  # 같은 세션에 휴대폰이 다시 붙으면 이전 연결을 닫는다
        try:
            await previous.close(code=4409, reason="replaced")
        except Exception:
            pass
    await ws.send_json({"type": "config", "width": settings.width, "height": settings.height,
                        "quality": settings.jpeg_quality, "max_fps": settings.max_fps})
    if previous is None:
        asyncio.create_task(relay.notify(sid, "connected"))

    tasks: set[asyncio.Task] = set()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                continue
            ts = now_ms()  # stream이 프레임을 받은 시각
            session.received += 1
            frame_id, jpeg = _parse_frame(text, settings.max_frame_bytes)
            if jpeg is None:
                session.dropped_invalid += 1
                session.record(None)
                await _ack(session, ws, frame_id, "invalid_frame")
                continue
            if session.busy:  # vision 응답을 기다리는 중: 큐에 쌓지 않고 버린다
                session.dropped_busy += 1
                session.record(None)
                await _ack(session, ws, frame_id, "busy")
                continue
            session.busy = True
            task = asyncio.create_task(_process(relay, session, ws, frame_id, jpeg, ts))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        if session.phone is ws:
            session.phone = None
            session.busy = False
            session.last_active = time.monotonic()
            asyncio.create_task(relay.notify(sid, "disconnected"))
