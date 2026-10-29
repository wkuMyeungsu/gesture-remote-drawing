"""세션 상태, vision 전달(④), web 알림(⑧).

처리 규칙 (docs/architecture.html):
- 최신 프레임만 처리한다. 휴대폰은 ack를 받은 뒤 다음 프레임을 보내고,
  stream은 vision 응답을 기다리는 중에 들어온 프레임을 큐에 쌓지 않고 버린다.
- vision 요청이 시간 제한(기본 200ms)을 넘으면 그 프레임은 버린다.
- ts: stream이 프레임을 받은 시각 (unix ms). vision 이후 모든 메시지에 그대로 전달된다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import WebSocket

from .config import Settings

log = logging.getLogger("uvicorn.error")
LOG_EVERY = 30.0  # 같은 오류는 이 간격으로만 로그를 남긴다


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Session:
    sid: str
    phone: WebSocket | None = None
    busy: bool = False  # vision 응답을 기다리는 중 (구조도: "세션별 처리 중 여부")
    connected_at: float = field(default_factory=time.time)

    received: int = 0
    forwarded: int = 0  # vision이 200으로 답한 수
    dropped_busy: int = 0  # 처리 중에 들어와 버린 수
    dropped_invalid: int = 0  # 형식이 틀려 버린 수
    dropped_vision: int = 0  # vision 시간 초과·오류로 버린 수
    last_vision_ms: float | None = None
    last_error: str | None = None

    def stats(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "phone_connected": self.phone is not None,
            "busy": self.busy,
            "received": self.received,
            "forwarded": self.forwarded,
            "dropped_busy": self.dropped_busy,
            "dropped_invalid": self.dropped_invalid,
            "dropped_vision": self.dropped_vision,
            "last_vision_ms": self.last_vision_ms,
            "last_error": self.last_error,
        }


class Relay:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.sessions: dict[str, Session] = {}
        self._last_log: dict[str, float] = {}

    def session(self, sid: str) -> Session:
        if sid not in self.sessions:
            self.sessions[sid] = Session(sid)
        return self.sessions[sid]

    async def forward(self, session: Session, frame_id: int, jpeg: bytes, ts: int) -> str | None:
        """④ JPEG 한 장을 vision에 보낸다. 성공하면 None, 버렸으면 이유를 돌려준다."""
        url = self.settings.vision_url
        if not url:
            return "vision_disabled"
        headers = {"Content-Type": "image/jpeg", "X-Session-Id": session.sid, "X-Frame-Id": str(frame_id), "X-Ts": str(ts)}
        started = time.perf_counter()
        limit = self.settings.timeout_ms / 1000
        try:
            # httpx의 timeout은 연결·읽기 단계마다 따로 걸린다. 요청 전체를 시간 제한 안에 끝내도록 한 번 더 감싼다
            r = await asyncio.wait_for(self.client.post(url, content=jpeg, headers=headers, timeout=limit), timeout=limit)
        except (httpx.TimeoutException, asyncio.TimeoutError):
            return self._fail(session, "vision_timeout", f"vision 응답이 {self.settings.timeout_ms}ms를 넘음")
        except httpx.HTTPError as e:
            return self._fail(session, "vision_unreachable", f"vision 연결 실패 ({type(e).__name__})")
        session.last_vision_ms = round((time.perf_counter() - started) * 1000, 1)
        if r.status_code != 200:
            return self._fail(session, f"vision_{r.status_code}", f"vision 응답 {r.status_code}: {r.text[:120]}")
        session.forwarded += 1
        return None

    def _fail(self, session: Session, reason: str, message: str) -> str:
        session.dropped_vision += 1
        session.last_error = message
        self._log(session.sid, message)
        return reason

    async def notify(self, sid: str, event: str) -> None:
        """⑧ 휴대폰 연결·끊김을 web에 알린다. 실패해도 프레임 처리는 계속한다."""
        url = self.settings.events_url
        if not url:
            return
        try:
            r = await self.client.post(url, json={"sid": sid, "event": event}, timeout=self.settings.timeout_ms / 1000)
            if r.status_code != 200:
                self._log(sid, f"web 알림({event}) 응답 {r.status_code}")
        except httpx.HTTPError as e:
            self._log(sid, f"web 알림({event}) 실패 ({type(e).__name__})")

    def _log(self, sid: str, message: str) -> None:
        now = time.monotonic()
        if now - self._last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[message] = now
            log.warning("[%s] %s (같은 내용은 %d초마다 기록)", sid[:8], message, int(LOG_EVERY))
