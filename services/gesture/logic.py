"""gesture 핵심 로직: 좌표 보정 → 동작 판별 → 동작 → 기능 대응.

웹 코드가 없어서 녹화한 좌표로 오프라인 테스트할 수 있다 (test_gesture_offline.py).
입출력 형식은 docs/contracts/README.md의 ⑤(vision → gesture), ⑥(gesture → web)을 따른다.
"""
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

WRIST = 0
INDEX_TIP = 8
# (끝, 가운데 마디): 검지, 중지, 약지, 새끼. 엄지는 이 판별이 불안정해서 뺀다.
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]

POSES = {
    (True, False, False, False): "index_up",
    (True, True, False, False): "two_fingers",
    (True, True, True, True): "open_palm",
    (False, False, False, False): "fist",
}

# 프레임마다 좌표와 함께 보내는 기능. 나머지는 동작이 바뀔 때 한 번만 보낸다.
CONTINUOUS = {"draw", "hover"}

MAPPING_PATH = Path(__file__).with_name("mapping.json")


@dataclass
class Config:
    mirror: bool = False         # 좌우 반전 (x = 1 - x). 계약상 vision이 이미 뒤집어 보내므로 기본은 끈다
    zone_lo: float = 0.15        # 이 범위(가운데 70%)를 캔버스 전체로 늘린다
    zone_hi: float = 0.85
    alpha: float = 0.4           # EMA. 낮을수록 부드럽지만 느리다
    extend_ratio: float = 1.1    # 끝이 마디보다 손목에서 10% 이상 멀면 펴진 손가락
    hold_frames: int = 4         # 새 동작은 연속 N프레임 보여야 인정
    lost_grace_frames: int = 3   # 손이 이만큼 연속으로 안 보여야 획을 끝낸다
    fist_hold_ms: int = 1000     # 주먹을 이만큼 유지해야 fist_hold (clear)


def load_mapping(path=MAPPING_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 1. 좌표 보정 ───────────────────────────────────────────

def to_canvas(v, lo, hi):
    return min(max((v - lo) / (hi - lo), 0.0), 1.0)


class Smoother:
    """검지 끝 좌표의 떨림을 줄이는 EMA. 손이 사라지면 reset해서 다음 획이 튀지 않게 한다."""

    def __init__(self, alpha):
        self.alpha = alpha
        self.pos = None

    def reset(self):
        self.pos = None

    def update(self, x, y):
        if self.pos is None:
            self.pos = (x, y)
        else:
            a = self.alpha
            sx, sy = self.pos
            self.pos = (a * x + (1 - a) * sx, a * y + (1 - a) * sy)
        return self.pos


# ── 2. 동작 판별 ───────────────────────────────────────────

def extended(lm, tip, pip, ratio):
    d = lambda a: ((lm[a][0] - lm[WRIST][0]) ** 2 + (lm[a][1] - lm[WRIST][1]) ** 2) ** 0.5
    return d(tip) > d(pip) * ratio


def classify(lm, ratio=1.1):
    fingers = tuple(extended(lm, tip, pip, ratio) for tip, pip in FINGERS)
    return POSES.get(fingers, "unknown")


# ── 3. 동작 → 기능 대응 (세션 하나의 상태) ─────────────────

class Session:
    """PC·휴대폰 한 쌍의 상태. 프레임 하나를 받아 web에 보낼 명령 목록을 돌려준다."""

    def __init__(self, sid, mapping, config):
        self.sid = sid
        self.mapping = mapping
        self.cfg = config
        self.smoother = Smoother(config.alpha)
        self.gesture = None          # 안정화된 현재 동작
        self._candidate = None       # 바뀔 후보 동작과 연속 프레임 수
        self._count = 0
        self._fist_since = None      # 주먹으로 바뀐 시각 (ts, ms)
        self._missing = 0            # 손이 연속으로 안 보인 프레임 수
        self._last_gesture = None    # 마지막으로 내보낸 동작 (1회 기능 중복 방지)
        self._pen_down = False       # draw 중인지 (획 경계에 up을 보내기 위해)
        self.last_seen = time.monotonic()

    def update(self, frame_id, ts, points, score):
        self.last_seen = time.monotonic()
        if points is None:
            return self._on_no_hand(frame_id, ts)
        self._missing = 0

        x, y = points[INDEX_TIP][0], points[INDEX_TIP][1]
        if self.cfg.mirror:
            x = 1.0 - x
        sx, sy = self.smoother.update(x, y)
        xy = (to_canvas(sx, self.cfg.zone_lo, self.cfg.zone_hi),
              to_canvas(sy, self.cfg.zone_lo, self.cfg.zone_hi))

        self._stabilize(classify(points, self.cfg.extend_ratio), ts)
        if self.gesture is None:
            return []
        gesture = self.gesture
        if gesture == "fist" and ts - self._fist_since >= self.cfg.fist_hold_ms:
            gesture = "fist_hold"
        return self._emit(gesture, score, frame_id, ts, xy)

    def _stabilize(self, raw, ts):
        # unknown은 현재 동작을 유지한다. 한 프레임 오인식으로 선이 끊기지 않게 연속 N프레임을 요구한다.
        if raw == "unknown" or raw == self.gesture:
            self._candidate, self._count = None, 0
            return
        if raw == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = raw, 1
        if self._count >= self.cfg.hold_frames:
            self.gesture = raw
            self._candidate, self._count = None, 0
            if raw == "fist":
                self._fist_since = ts

    def _on_no_hand(self, frame_id, ts):
        # 1~2프레임 놓친 건 기다리고, 그 이상이면 획을 끝내고 보정·판별 상태를 비운다.
        self._missing += 1
        if self._missing <= self.cfg.lost_grace_frames:
            return []
        if self._missing == self.cfg.lost_grace_frames + 1:
            self.smoother.reset()
            self.gesture = None
            self._candidate, self._count = None, 0
        return self._emit("none", 1.0, frame_id, ts, None)

    def _emit(self, gesture, confidence, frame_id, ts, xy):
        action = self.mapping.get(gesture)
        changed = gesture != self._last_gesture
        self._last_gesture = gesture
        out = []

        if self._pen_down and action != "draw":
            out.append(self._command(gesture, confidence, frame_id, ts, "up"))
            self._pen_down = False
            if action == "up":
                return out

        if action is None:
            return out
        if action in CONTINUOUS:
            out.append(self._command(gesture, confidence, frame_id, ts, action, xy))
            self._pen_down = action == "draw"
        elif changed:
            out.append(self._command(gesture, confidence, frame_id, ts, action))
        return out

    def _command(self, gesture, confidence, frame_id, ts, action, xy=None):
        cmd = {
            "sid": self.sid,
            "frame_id": frame_id,
            "ts": ts,
            "gesture": gesture,
            "confidence": round(confidence, 3),
            "action": action,
        }
        if xy is not None:
            cmd["x"], cmd["y"] = round(xy[0], 4), round(xy[1], 4)
        return cmd


# ── 입력 검사와 세션 관리 ─────────────────────────────────

def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_landmarks(msg):
    """⑤ 메시지를 검사해 (sid, frame_id, ts, points, score)로 돌려준다. 형식이 틀리면 ValueError."""
    if not isinstance(msg, dict):
        raise ValueError("body must be a JSON object")
    sid = msg.get("sid")
    if not isinstance(sid, str) or not sid:
        raise ValueError("sid must be a non-empty string")
    frame_id, ts = msg.get("frame_id"), msg.get("ts")
    if not isinstance(frame_id, int) or isinstance(frame_id, bool):
        raise ValueError("frame_id must be an integer")
    if not _is_number(ts):
        raise ValueError("ts must be a number")

    hand = msg.get("hand")
    if hand is None:
        return sid, frame_id, ts, None, 0.0
    if not isinstance(hand, dict):
        raise ValueError("hand must be an object or null")
    points = hand.get("points")
    if not isinstance(points, list) or len(points) < 21:
        raise ValueError("hand.points must have 21 points")
    for p in points:
        if not isinstance(p, list) or len(p) < 2 or not all(_is_number(v) for v in p[:2]):
            raise ValueError("each point must be [x, y, ...]")
    score = hand.get("score", 1.0)
    if not _is_number(score):
        raise ValueError("hand.score must be a number")
    return sid, frame_id, ts, points, float(score)


class SessionStore:
    """sid마다 Session을 하나씩 둔다. 오래 조용한 세션은 지운다."""

    def __init__(self, mapping=None, config=None, idle_seconds=300):
        self.mapping = mapping if mapping is not None else load_mapping()
        self.config = config or Config()
        self.idle_seconds = idle_seconds
        self._sessions = {}
        self._lock = threading.Lock()

    def process(self, msg):
        sid, frame_id, ts, points, score = parse_landmarks(msg)
        with self._lock:
            self._prune()
            session = self._sessions.get(sid)
            if session is None:
                session = self._sessions[sid] = Session(sid, self.mapping, self.config)
            return session.update(frame_id, ts, points, score)

    def _prune(self):
        now = time.monotonic()
        for sid in [s for s, v in self._sessions.items() if now - v.last_seen > self.idle_seconds]:
            del self._sessions[sid]
