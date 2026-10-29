"""환경변수 설정. 루트 .env는 모든 서비스가 같이 읽으므로 stream 전용 값은 STREAM_ 접두사를 붙인다."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    vision_url: str  # ④ 프레임을 보낼 곳. 비우면 보내지 않는다 (카메라 페이지만 확인할 때)
    events_url: str  # ⑧ 연결·끊김을 알릴 곳. 비우면 알리지 않는다
    timeout_ms: int  # ④ 응답을 기다리는 최대 시간. 넘으면 그 프레임은 버린다
    width: int  # 휴대폰이 보낼 프레임의 긴 변·짧은 변 (가로로 찍으면 width×height)
    height: int
    jpeg_quality: float
    max_fps: int
    max_frame_bytes: int
    dev_sid: str  # 개발용. 정하면 X-Session-Id 헤더가 없을 때 ?sid= 또는 이 값을 세션으로 쓴다 (security 없이 테스트)


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def load_settings() -> Settings:
    return Settings(
        vision_url=os.getenv("STREAM_VISION_URL", "http://vision:8003/frames").strip(),
        events_url=os.getenv("STREAM_EVENTS_URL", "http://web:8000/events").strip(),
        timeout_ms=int(_env("STREAM_TIMEOUT_MS", "200")),
        width=int(_env("STREAM_WIDTH", "320")),
        height=int(_env("STREAM_HEIGHT", "240")),
        jpeg_quality=float(_env("STREAM_JPEG_QUALITY", "0.6")),
        max_fps=int(_env("STREAM_MAX_FPS", "15")),
        max_frame_bytes=int(_env("STREAM_MAX_FRAME_BYTES", str(512 * 1024))),
        dev_sid=os.getenv("STREAM_DEV_SID", "").strip(),
    )
