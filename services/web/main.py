"""web: PC 그림판 페이지 제공, ⑥ 기능(action)·⑧ 연결 이벤트를 PC 브라우저로 WebSocket 중계.

메시지 형식은 docs/contracts/README.md를 따른다.
"""
import asyncio
import io
import os
from collections import defaultdict
from pathlib import Path
from typing import Literal

import qrcode
import qrcode.image.svg
import uvicorn
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

SERVICE = os.environ.get("SERVICE_NAME", "web")
PORT = int(os.environ.get("PORT", "8000"))
STATIC = Path(__file__).parent / "static"
SEND_TIMEOUT = 0.2  # 내부 요청 타임아웃 200ms에 맞춘다

app = FastAPI(title="web")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# sid → 그 세션을 보고 있는 PC 브라우저 WebSocket들
boards: dict[str, set[WebSocket]] = defaultdict(set)


class ActionIn(BaseModel):
    """⑥ gesture → web"""

    sid: str
    action: str
    frame_id: int | None = None
    ts: int | None = None
    gesture: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    params: dict | None = None

    @model_validator(mode="after")
    def needs_point(self):
        if self.action in ("draw", "erase", "hover") and (self.x is None or self.y is None):
            raise ValueError(f"'{self.action}'에는 x, y가 필요하다")
        return self


class EventIn(BaseModel):
    """⑧ stream → web"""

    sid: str
    event: Literal["connected", "disconnected"]


@app.exception_handler(RequestValidationError)
async def bad_request(_: Request, exc: RequestValidationError):
    # 계약: 형식이 틀리면 400 (FastAPI 기본값 422 대신)
    return JSONResponse({"ok": False, "error": str(exc.errors())}, status_code=400)


async def broadcast(sid: str, message: dict) -> int:
    """같은 sid의 PC 브라우저에 보내고, 받은 브라우저 수를 돌려준다. 실패한 연결은 뺀다."""
    sockets = list(boards.get(sid, ()))
    results = await asyncio.gather(
        *(asyncio.wait_for(ws.send_json(message), SEND_TIMEOUT) for ws in sockets),
        return_exceptions=True,
    )
    for ws, result in zip(sockets, results):
        if isinstance(result, Exception):
            boards[sid].discard(ws)
    return sum(not isinstance(r, Exception) for r in results)


@app.get("/health")
async def health():
    return {"service": SERVICE, "status": "ok"}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/qr.svg")
async def qr_svg(data: str = Query(min_length=1, max_length=512)):
    """페어링 QR. 브라우저가 외부 CDN 없이 쓸 수 있도록 서버에서 SVG로 만든다."""
    buf = io.BytesIO()
    qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.post("/actions")
async def actions(body: ActionIn):
    message = {"type": "action", **body.model_dump(exclude={"sid"}, exclude_none=True)}
    delivered = await broadcast(body.sid, message)
    return {"ok": True, "delivered": delivered}


@app.post("/events")
async def events(body: EventIn):
    delivered = await broadcast(body.sid, {"type": "event", "event": body.event})
    return {"ok": True, "delivered": delivered}


@app.websocket("/ws/board")
async def ws_board(ws: WebSocket, sid: str = Query(min_length=1)):
    await ws.accept()
    boards[sid].add(ws)
    try:
        while True:
            await ws.receive_text()  # 브라우저 → 서버 메시지는 아직 없다. 연결 유지용.
    except WebSocketDisconnect:
        pass
    finally:
        boards[sid].discard(ws)
        if not boards[sid]:
            boards.pop(sid, None)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
