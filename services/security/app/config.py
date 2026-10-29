"""환경변수 설정. 루트 .env는 모든 서비스가 같이 읽으므로 security 전용 값은 SECURITY_ 접두사를 붙인다."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    token_ttl: int  # QR 1회용 토큰 유효 시간 (초). 계약: 60
    session_ttl: int  # 세션과 휴대폰 sid 쿠키 유효 시간 (초)
    max_sessions: int  # 동시에 가질 수 있는 세션 수. 넘으면 새 발급을 거절한다 (메모리 보호)
    public_host: str  # QR 주소에 넣을 호스트 (예: 192.168.0.12). 비우면 인증서 주소, 그것도 없으면 요청의 Host 헤더
    cookie_secure: bool  # sid 쿠키에 Secure를 붙인다. HTTPS 없이 테스트할 때만 끈다


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def load_settings() -> Settings:
    return Settings(
        token_ttl=int(_env("SECURITY_TOKEN_TTL", "60")),
        session_ttl=int(_env("SECURITY_SESSION_TTL", str(3 * 60 * 60))),
        max_sessions=int(_env("SECURITY_MAX_SESSIONS", "1000")),
        # PC 브라우저를 https://localhost로 열어도 QR에는 휴대폰이 찾아올 수 있는 PC IP가 들어가게 한다
        public_host=os.getenv("SECURITY_PUBLIC_HOST", "").strip() or os.getenv("SECURITY_CERT_HOST", "").strip(),
        cookie_secure=_env("SECURITY_COOKIE_SECURE", "1") not in ("0", "false", "no"),
    )
