"""vision (8003): 프레임을 받아 전처리하고, MediaPipe Hands로 손 좌표 21개를 찾아 gesture로 보낸다.

연결 (docs/architecture.html, docs/contracts/README.md):
  ④ stream → vision:  POST /frames  (JPEG 바이트, X-Session-Id · X-Frame-Id · X-Ts 헤더)
  ⑤ vision → gesture: POST http://gesture:8004/landmarks
vision은 ⑤를 보낸 뒤에 ④에 {"ok": true}로 답한다. stream은 이 응답을 받아야 다음 프레임을 보낸다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .detector import BadImage, HandDetector

log = logging.getLogger("uvicorn.error")
LOG_EVERY = 30.0  # 같은 오류는 이 간격으로만 로그를 남긴다


@dataclass
class Stats:
    received: int = 0
    hands: int = 0  # 손을 찾은 프레임 수
    no_hand: int = 0
    invalid: int = 0  # 헤더·JPEG가 틀려 400으로 답한 수
    sent: int = 0  # gesture가 200으로 답한 수
    send_failed: int = 0
    last_detect_ms: float | None = None
    last_send_ms: float | None = None
    avg_detect_ms: float | None = None  # 최근 프레임 위주 이동 평균
    last_error: str | None = None

    def add_detect(self, ms: float) -> None:
        self.last_detect_ms = round(ms, 1)
        self.avg_detect_ms = round(ms if self.avg_detect_ms is None else self.avg_detect_ms * 0.9 + ms * 0.1, 1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.client = httpx.AsyncClient()
    app.state.detector = HandDetector(settings)
    app.state.stats = Stats()
    app.state.last_log = {}
    log.info("vision 시작: gesture=%s, 시간 제한 %dms, 좌우 반전 %s, 모델 %s, 최소 신뢰도 %.2f",
             settings.gesture_url or "(보내지 않음)", settings.timeout_ms, "켬" if settings.mirror else "끔",
             "full" if settings.model_complexity else "lite", settings.min_detection)
    yield
    await app.state.client.aclose()
    app.state.detector.close()


app = FastAPI(title="vision", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    s = request.app.state
    return {"service": "vision", "status": "ok", "gesture_url": s.settings.gesture_url or None}


@app.get("/stats")
async def stats(request: Request):
    """개발·디버그용: 처리 현황과 처리 시간."""
    return request.app.state.stats.__dict__


def _bad(stats: Stats, message: str) -> JSONResponse:
    stats.invalid += 1
    stats.last_error = message
    return JSONResponse({"ok": False, "error": message}, status_code=400)


def _int_header(request: Request, name: str) -> int | None:
    try:
        return int(request.headers.get(name, "").strip())
    except ValueError:
        return None


async def _read_jpeg(request: Request, settings: Settings) -> bytes | str:
    """본문을 읽는다. 너무 크거나 비었으면 오류 문구를 돌려준다."""
    length = _int_header(request, "content-length")
    if length is not None and length > settings.max_frame_bytes:
        return f"프레임이 너무 큼 ({length}바이트)"
    body = await request.body()
    if not body:
        return "본문이 비어 있음"
    if len(body) > settings.max_frame_bytes:
        return f"프레임이 너무 큼 ({len(body)}바이트)"
    return body


async def _detect(request: Request, body: bytes) -> dict | None:
    """MediaPipe는 CPU를 쓰므로 이벤트 루프를 막지 않게 스레드에서 돌린다."""
    s = request.app.state
    started = time.perf_counter()
    hand = await asyncio.to_thread(s.detector.detect, body)
    s.stats.add_detect((time.perf_counter() - started) * 1000)
    if hand is None:
        s.stats.no_hand += 1
    else:
        s.stats.hands += 1
    return hand


@app.post("/frames")
async def frames(request: Request):
    """④ JPEG 한 장 → 손 좌표 → ⑤ gesture로 보낸 뒤 답한다."""
    s = request.app.state
    settings: Settings = s.settings
    s.stats.received += 1

    sid = request.headers.get("x-session-id", "").strip()
    frame_id = _int_header(request, "x-frame-id")
    ts = _int_header(request, "x-ts")
    if not sid or frame_id is None or ts is None:
        return _bad(s.stats, "X-Session-Id, X-Frame-Id, X-Ts 헤더가 필요함")

    body = await _read_jpeg(request, settings)
    if isinstance(body, str):
        return _bad(s.stats, body)
    try:
        hand = await _detect(request, body)
    except BadImage as e:
        return _bad(s.stats, str(e))

    error = await _send(request.app, {"sid": sid, "frame_id": frame_id, "ts": ts, "hand": hand})
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=502)
    return {"ok": True}


@app.post("/detect")
async def detect(request: Request):
    """개발용: JPEG 한 장의 손 좌표를 gesture로 보내지 않고 바로 돌려준다. 형식은 ⑤의 "hand"와 같다."""
    s = request.app.state
    body = await _read_jpeg(request, s.settings)
    if isinstance(body, str):
        return JSONResponse({"ok": False, "error": body}, status_code=400)
    try:
        hand = await _detect(request, body)
    except BadImage as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "hand": hand, "detect_ms": s.stats.last_detect_ms}


async def _send(app: FastAPI, payload: dict) -> str | None:
    """⑤ 손 좌표를 gesture에 보낸다. 성공(또는 보낼 곳 없음)이면 None, 실패하면 오류 문구."""
    s = app.state
    url = s.settings.gesture_url
    if not url:
        return None
    limit = s.settings.timeout_ms / 1000
    started = time.perf_counter()
    try:
        # httpx의 timeout은 단계마다 따로 걸리므로 요청 전체를 한 번 더 감싼다
        r = await asyncio.wait_for(s.client.post(url, json=payload, timeout=limit), timeout=limit)
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return _send_failed(app, payload["sid"], f"gesture 응답이 {s.settings.timeout_ms}ms를 넘음")
    except httpx.HTTPError as e:
        return _send_failed(app, payload["sid"], f"gesture 연결 실패 ({type(e).__name__})")
    s.stats.last_send_ms = round((time.perf_counter() - started) * 1000, 1)
    if r.status_code != 200:
        return _send_failed(app, payload["sid"], f"gesture 응답 {r.status_code}: {r.text[:120]}")
    s.stats.sent += 1
    return None


def _send_failed(app: FastAPI, sid: str, message: str) -> str:
    s = app.state
    s.stats.send_failed += 1
    s.stats.last_error = message
    now = time.monotonic()
    if now - s.last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
        s.last_log[message] = now
        log.warning("[%s] %s (같은 내용은 %d초마다 기록)", sid[:8], message, int(LOG_EVERY))
    return message
