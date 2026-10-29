"""전처리와 손 좌표 추출.

순서: JPEG 디코딩 → (크면) 축소 → 좌우 반전 → BGR→RGB → MediaPipe Hands → 손 하나 고르기.
결과는 docs/contracts/README.md ⑤의 "hand" 형식 그대로 돌려준다.

좌표가 튀지 않게 하는 두 가지:
- 추적 모드: 세션(휴대폰)마다 MediaPipe Hands를 하나씩 두고 static_image_mode=False로 돌린다.
  앞 프레임에서 찾은 손 영역을 이어서 쓰므로, 프레임마다 손바닥 검출부터 다시 하는 정지 이미지 모드보다
  점 위치가 안정적이고 빠르다. 추적이 끊기면(신뢰도 < min_tracking) 그 프레임에서 다시 찾는다.
- 같은 손 고르기: 손이 여럿이면 앞 프레임에서 보낸 손에 가장 가까운 손을 고른다.
  score만 보면 두 손(또는 오검출) 사이를 프레임마다 오가며 좌표가 크게 점프한다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from .config import Settings

NUM_POINTS = 21
ANCHOR = 9  # 같은 손인지 비교할 점: 중지 뿌리(손바닥 가운데). 손가락을 굽혀도 거의 안 움직인다
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


def pick_hand(results: Any, mirrored: bool, prev: tuple[float, float] | None = None) -> dict | None:
    """MediaPipe 결과에서 손 하나를 ⑤ 형식으로 만든다. 없으면 None.

    prev(앞 프레임에서 보낸 손의 ANCHOR 점 x, y)가 있으면 그 점에 가장 가까운 손을, 없으면 score가 가장 높은 손을 고른다.
    """
    landmarks = getattr(results, "multi_hand_landmarks", None) or []
    handedness = getattr(results, "multi_handedness", None) or []
    best = None
    best_key = None
    for marks, hand in zip(landmarks, handedness):
        if len(marks.landmark) != NUM_POINTS:
            continue
        label = hand.classification[0].label
        score = float(hand.classification[0].score)
        if prev is not None:
            a = marks.landmark[ANCHOR]
            key = (a.x - prev[0]) ** 2 + (a.y - prev[1]) ** 2  # 작을수록 좋음
        else:
            key = -score
        if best_key is None or key < best_key:
            best, best_key = (marks, score, label), key
    if best is None:
        return None
    marks, score, label = best
    # MediaPipe는 거울처럼 뒤집힌(셀카) 이미지를 가정하고 왼손·오른손을 정한다. 반전하지 않았으면 바꿔 준다
    if not mirrored:
        label = _SWAP.get(label, label)
    points = [[round(_clamp01(p.x), 4), round(_clamp01(p.y), 4), round(float(p.z), 4)] for p in marks.landmark]
    return {"handedness": label, "score": round(score, 3), "points": points}


class HandDetector:
    """MediaPipe Hands 래퍼.

    static=True: 프레임마다 독립으로 찾는다 (사진 한 장용, POST /detect).
    static=False: 앞 프레임의 손을 이어서 추적한다 (영상용). 한 세션의 프레임만 넣어야 한다.
    MediaPipe 그래프는 여러 스레드에서 동시에 부를 수 없으므로 잠금으로 한 번에 한 프레임만 처리한다.
    """

    def __init__(self, settings: Settings, static: bool = True):
        import mediapipe as mp  # 무거워서 실제로 쓸 때만 불러온다 (테스트에서 가짜로 바꿀 수 있게)

        self.settings = settings
        self.static = static
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=static,
            max_num_hands=settings.max_hands,
            model_complexity=settings.model_complexity,
            min_detection_confidence=settings.min_detection,
            min_tracking_confidence=settings.min_tracking,
        )
        self._lock = threading.Lock()
        self._prev: tuple[float, float] | None = None  # 앞 프레임에서 보낸 손의 ANCHOR 점

    def detect(self, jpeg: bytes) -> dict | None:
        """JPEG 한 장 → ⑤의 hand (없으면 None). JPEG가 틀리면 BadImage."""
        rgb = preprocess(decode(jpeg), self.settings)
        with self._lock:
            results = self._hands.process(rgb)
            hand = pick_hand(results, self.settings.mirror, None if self.static else self._prev)
            if not self.static:
                self._prev = tuple(hand["points"][ANCHOR][:2]) if hand else None
        return hand

    def close(self) -> None:
        with self._lock:
            self._hands.close()


class SessionDetectors:
    """세션(sid)마다 추적 모드 HandDetector를 하나씩 둔다.

    세션마다 따로 두는 이유: 추적은 앞 프레임을 기억하므로 다른 휴대폰의 프레임이 섞이면 엉뚱한 곳을 이어서 찾는다.
    stream은 세션마다 한 번에 한 프레임만 보내므로 한 세션 안에서는 순서가 지켜진다.
    session_idle_s 동안 프레임이 없으면 정리하고, max_sessions를 넘으면 가장 오래 쉰 세션부터 정리한다.
    """

    def __init__(self, settings: Settings, factory: Callable[[], Any]):
        self.settings = settings
        self._factory = factory
        self._items: dict[str, list] = {}  # sid → [detector, 마지막 사용 시각]
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._items)

    def _get(self, sid: str):
        now = time.monotonic()
        closing = []
        with self._lock:
            for key, (det, used) in list(self._items.items()):
                if key != sid and now - used > self.settings.session_idle_s:
                    closing.append(self._items.pop(key)[0])
            item = self._items.get(sid)
            if item is None:
                while len(self._items) >= max(1, self.settings.max_sessions):
                    oldest = min(self._items, key=lambda k: self._items[k][1])
                    closing.append(self._items.pop(oldest)[0])
                item = self._items[sid] = [self._factory(), now]
            item[1] = now
            det = item[0]
        for old in closing:
            old.close()
        return det

    def detect(self, sid: str, jpeg: bytes) -> dict | None:
        return self._get(sid).detect(jpeg)

    def close(self) -> None:
        with self._lock:
            items, self._items = list(self._items.values()), {}
        for det, _ in items:
            det.close()
