"""전처리와 손 좌표 추출.

순서: JPEG 디코딩 → (크면) 축소 → 좌우 반전 → BGR→RGB → MediaPipe Hands → score가 가장 높은 손 하나.
결과는 docs/contracts/README.md ⑤의 "hand" 형식 그대로 돌려준다.
"""
from __future__ import annotations

import threading
from typing import Any

import cv2
import numpy as np

from .config import Settings

NUM_POINTS = 21
_SWAP = {"Left": "Right", "Right": "Left"}


class BadImage(ValueError):
    """JPEG로 읽을 수 없는 본문."""


def decode(jpeg: bytes) -> np.ndarray:
    """JPEG 바이트 → BGR 배열. 틀리면 BadImage."""
    if len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8":
        raise BadImage("JPEG가 아님")
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise BadImage("JPEG 디코딩 실패")
    return image


def preprocess(image: np.ndarray, settings: Settings) -> np.ndarray:
    """BGR 배열 → MediaPipe에 넣을 RGB 배열 (축소, 좌우 반전)."""
    h, w = image.shape[:2]
    longest = max(h, w)
    if settings.max_side > 0 and longest > settings.max_side:
        scale = settings.max_side / longest
        image = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    if settings.mirror:
        image = cv2.flip(image, 1)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    rgb.flags.writeable = False  # MediaPipe가 복사하지 않고 참조한다
    return rgb


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def pick_hand(results: Any, mirrored: bool) -> dict | None:
    """MediaPipe 결과에서 score가 가장 높은 손 하나를 ⑤ 형식으로 만든다. 없으면 None."""
    landmarks = getattr(results, "multi_hand_landmarks", None) or []
    handedness = getattr(results, "multi_handedness", None) or []
    best = None
    for marks, hand in zip(landmarks, handedness):
        label = hand.classification[0].label
        score = float(hand.classification[0].score)
        if best is None or score > best[1]:
            best = (marks, score, label)
    if best is None:
        return None
    marks, score, label = best
    # MediaPipe는 거울처럼 뒤집힌(셀카) 이미지를 가정하고 왼손·오른손을 정한다. 반전하지 않았으면 바꿔 준다
    if not mirrored:
        label = _SWAP.get(label, label)
    points = [[round(_clamp01(p.x), 4), round(_clamp01(p.y), 4), round(float(p.z), 4)] for p in marks.landmark]
    if len(points) != NUM_POINTS:
        return None
    return {"handedness": label, "score": round(score, 3), "points": points}


class HandDetector:
    """MediaPipe Hands 래퍼. 프레임마다 독립으로 찾는다 (static_image_mode, vision은 세션 상태가 없다).

    MediaPipe 그래프는 여러 스레드에서 동시에 부를 수 없으므로 잠금으로 한 번에 한 프레임만 처리한다.
    """

    def __init__(self, settings: Settings):
        import mediapipe as mp  # 무거워서 실제로 쓸 때만 불러온다 (테스트에서 가짜로 바꿀 수 있게)

        self.settings = settings
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=True,
            max_num_hands=settings.max_hands,
            model_complexity=settings.model_complexity,
            min_detection_confidence=settings.min_detection,
        )
        self._lock = threading.Lock()

    def detect(self, jpeg: bytes) -> dict | None:
        """JPEG 한 장 → ⑤의 hand (없으면 None). JPEG가 틀리면 BadImage."""
        rgb = preprocess(decode(jpeg), self.settings)
        with self._lock:
            results = self._hands.process(rgb)
        return pick_hand(results, self.settings.mirror)

    def close(self) -> None:
        with self._lock:
            self._hands.close()
