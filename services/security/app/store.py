"""세션 저장소 (메모리). security를 다시 시작하면 모든 세션이 사라지고 QR을 새로 찍어야 한다.

- sid: 세션 ID (UUID). PC 브라우저가 받아 /ws/board?sid=…에 쓴다.
- token: QR에 들어가는 1회용 토큰. 휴대폰이 한 번 쓰면 폐기한다 → 세션 하나에 휴대폰 한 대.
- phone_key: 휴대폰 sid 쿠키에 들어가는 값. sid를 그대로 쿠키에 넣지 않아서,
  sid를 아는 쪽(PC 화면)이 휴대폰인 척 프레임을 보낼 수 없다.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .config import Settings


@dataclass
class Session:
    sid: str
    expires: float
    token: str | None
    token_expires: float
    phone_key: str | None = None


class SessionStore:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.settings, self.clock = settings, clock
        self.sessions: dict[str, Session] = {}
        self.by_token: dict[str, str] = {}
        self.by_phone: dict[str, str] = {}

    def create(self) -> Session | None:
        self.prune()
        if len(self.sessions) >= self.settings.max_sessions:
            return None
        now = self.clock()
        s = Session(sid=str(uuid.uuid4()), expires=now + self.settings.session_ttl,
                    token=secrets.token_urlsafe(12), token_expires=now + self.settings.token_ttl)
        self.sessions[s.sid] = s
        self.by_token[s.token] = s.sid
        return s

    def redeem(self, token: str) -> Session | None:
        """1회용 토큰을 휴대폰 키로 바꾼다. 만료됐거나 이미 쓴 토큰이면 None."""
        sid = self.by_token.pop(token, None)
        s = self.sessions.get(sid) if sid else None
        if s is None or s.token != token:
            return None
        s.token = None  # 성공·실패와 상관없이 한 번 확인한 토큰은 다시 못 쓴다
        if self.clock() > s.token_expires or self.clock() > s.expires:
            return None
        s.phone_key = secrets.token_urlsafe(32)
        self.by_phone[s.phone_key] = s.sid
        return s

    def by_cookie(self, phone_key: str) -> Session | None:
        sid = self.by_phone.get(phone_key)
        s = self.sessions.get(sid) if sid else None
        if s is None or self.clock() > s.expires:
            return None
        return s

    def prune(self) -> None:
        now = self.clock()
        for sid in [sid for sid, s in self.sessions.items() if now > s.expires]:
            s = self.sessions.pop(sid)
            self.by_token.pop(s.token or "", None)
            self.by_phone.pop(s.phone_key or "", None)
        for token in [t for t, sid in self.by_token.items() if now > self.sessions[sid].token_expires]:
            self.sessions[self.by_token.pop(token)].token = None
