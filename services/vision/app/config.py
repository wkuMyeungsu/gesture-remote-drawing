"""환경변수 설정. 루트 .env는 모든 서비스가 같이 읽으므로 vision 전용 값은 VISION_ 접두사를 붙인다."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gesture_url: str  # ⑤ 손 좌표를 보낼 곳. 비우면 보내지 않는다 (vision만 확인할 때)
    timeout_ms: int  # ⑤ 응답을 기다리는 최대 시간
    mirror: bool  # 좌우 반전. 휴대폰은 뒤집지 않은 프레임을 보내므로 기본으로 켠다 (반전은 vision에서 한 번만)
    max_side: int  # 긴 변이 이보다 크면 줄여서 처리한다 (좌표는 0~1이라 결과 형식은 같다)
    max_frame_bytes: int
    model_complexity: int  # MediaPipe Hands 모델. 0: lite(빠름), 1: full(정확)
    min_detection: float  # 손으로 인정할 최소 신뢰도
    max_hands: int  # 찾을 손 개수. 보낼 때는 score가 가장 높은 손 하나만


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _flag(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() not in ("0", "false", "no", "off")


def load_settings() -> Settings:
    return Settings(
        gesture_url=os.getenv("VISION_GESTURE_URL", "http://gesture:8004/landmarks").strip(),
        timeout_ms=int(_env("VISION_TIMEOUT_MS", "200")),
        mirror=_flag("VISION_MIRROR", True),
        max_side=int(_env("VISION_MAX_SIDE", "640")),
        max_frame_bytes=int(_env("VISION_MAX_FRAME_BYTES", str(1024 * 1024))),
        model_complexity=int(_env("VISION_MODEL_COMPLEXITY", "0")),
        min_detection=float(_env("VISION_MIN_DETECTION", "0.5")),
        max_hands=int(_env("VISION_MAX_HANDS", "2")),
    )
