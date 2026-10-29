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
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import WebSocket

from .config import Settings

log = logging.getLogger("uvicorn.error")
LOG_EVERY = 30.0  # 같은 오류는 이 간격으로만 로그를 남긴다
WINDOW = 10.0  # 처리 현황(초당 처리 수, 지연)을 계산하는 최근 시간 (초)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Session:
    sid: str
    phone: WebSocket | None = None
    busy: bool = False  # vision 응답을 기다리는 중 (구조도: "세션별 처리 중 여부")
    connected_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.monotonic)
    recent: deque = field(default_factory=deque)  # 최근 WINDOW초의 (시각, vision 응답 ms 또는 None=버림)

    received: int = 0
    forwarded: int = 0  # vision이 200으로 답한 수
    dropped_busy: int = 0  # 처리 중에 들어와 버린 수
    dropped_invalid: int = 0  # 형식이 틀려 버린 수
    dropped_vision: int = 0  # vision 시간 초과·오류로 버린 수
    last_vision_ms: float | None = None
    last_error: str | None = None

    def record(self, vision_ms: float | None) -> None:
        """프레임 하나의 결과를 기록한다. vision_ms가 None이면 버린 프레임."""
        now = time.monotonic()
        self.last_active = now
        self.recent.append((now, vision_ms))
        while self.recent and now - self.recent[0][0] > WINDOW:
            self.recent.popleft()

    def window(self) -> dict[str, Any]:
        """최근 WINDOW초: 초당 처리 수, vision 응답 시간(평균·p95), 버린 비율."""
        now = time.monotonic()
        items = [ms for t, ms in self.recent if now - t <= WINDOW]
        done = sorted(ms for ms in items if ms is not None)
        p95 = done[min(len(done) - 1, int(len(done) * 0.95))] if done else None
        return {
            "seconds": WINDOW,
            "fps": round(len(done) / WINDOW, 1),
            "vision_ms_avg": round(sum(done) / len(done), 1) if done else None,
            "vision_ms_p95": p95,
            "drop_rate": round(1 - len(done) / len(items), 2) if items else None,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "phone_connected": self.phone is not None,
            "busy": self.busy,
            "recent": self.window(),
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
        session.record(session.last_vision_ms)
        return None

    def _fail(self, session: Session, reason: str, message: str) -> str:
        session.dropped_vision += 1
        session.record(None)
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

    def prune(self, ttl: float) -> list[str]:
        """휴대폰이 끊긴 지 ttl초가 지난 세션을 지운다 (세션이 계속 쌓이지 않게)."""
        now = time.monotonic()
        old = [sid for sid, s in self.sessions.items() if s.phone is None and not s.busy and now - s.last_active > ttl]
        for sid in old:
            del self.sessions[sid]
        return old

    async def notify_all_disconnected(self) -> None:
        """stream이 꺼질 때 연결된 휴대폰을 모두 끊김으로 알린다 (그림판이 '연결됨'으로 남지 않게)."""
        sids = [s.sid for s in self.sessions.values() if s.phone is not None]
        if sids:
            await asyncio.gather(*(self.notify(sid, "disconnected") for sid in sids), return_exceptions=True)

    def _log(self, sid: str, message: str) -> None:
        now = time.monotonic()
        if now - self._last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[message] = now
            log.warning("[%s] %s (같은 내용은 %d초마다 기록)", sid[:8], message, int(LOG_EVERY))
