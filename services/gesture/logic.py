"""gesture 핵심 로직: 좌표 보정 → 동작 판별 → 동작 → 기능 대응.

웹 코드가 없어서 녹화한 좌표로 오프라인 테스트할 수 있다 (test_gesture_offline.py).
입출력 형식은 docs/contracts/README.md의 ⑤(vision → gesture), ⑥(gesture → web)을 따른다.
"""
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9
# (끝, 가운데 마디): 검지, 중지, 약지, 새끼. 엄지는 이 판별이 불안정해서 뺀다.
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]
# 판별에 쓰는 점. 이 중 하나라도 화면 가장자리에 닿으면 그 프레임은 판별하지 않는다.
CLASSIFY_POINTS = [WRIST] + [i for pair in FINGERS for i in pair]

POSES = {
    (True, False, False, False): "index_up",
    (True, True, False, False): "two_fingers",
    (True, True, True, True): "open_palm",
    (False, False, False, False): "fist",
}

# 프레임마다 좌표와 함께 보내는 기능. 나머지는 동작이 바뀔 때 한 번만 보낸다.
CONTINUOUS = {"draw", "erase", "hover"}
# 획을 남기는 기능. 다른 기능으로 바뀌면 up을 보내 획을 끊는다.
STROKES = {"draw", "erase"}

MAPPING_PATH = Path(__file__).with_name("mapping.json")


@dataclass
class Config:
    mirror: bool = False         # 좌우 반전 (x = 1 - x). 계약상 vision이 이미 뒤집어 보내므로 기본은 끈다
    zone_lo: float = 0.15        # 이 범위(가운데 70%)를 캔버스 전체로 늘린다
    zone_hi: float = 0.85
    min_cutoff: float = 1.0      # One Euro: 손이 멈췄을 때의 보정 세기(Hz). 낮을수록 떨림이 줄지만 느리다
    beta: float = 10.0           # One Euro: 손이 빠를수록 보정을 얼마나 풀지. 높을수록 빠른 움직임에 덜 늦는다
    extend_ratio: float = 1.1    # 접힌 손가락은 끝이 마디보다 손목에서 10% 이상 멀어져야 펴진 것으로 본다
    fold_ratio: float = 0.95     # 펴진 손가락은 끝이 마디의 95% 안으로 들어와야 접힌 것으로 본다 (사이 값은 직전 상태 유지)
    edge_margin: float = 0.02    # 판별에 쓰는 점이 화면 테두리에서 이만큼 안쪽에 없으면 그 프레임은 unknown
    pinch_on: float = 0.3        # 엄지 끝–검지 끝 거리가 손바닥 길이(손목–중지 뿌리)의 30% 안이면 맞닿음
    pinch_off: float = 0.45      # 맞닿은 뒤에는 45%를 넘어야 떨어진 것으로 본다
    hold_frames: int = 4         # 새 동작은 연속 N프레임 보여야 인정
    lost_grace_frames: int = 3   # 손이 이만큼 연속으로 안 보여야 획을 끝낸다
    fist_hold_ms: int = 1000     # 주먹을 이만큼 유지해야 fist_hold (clear)
    send_landmarks: bool = False # 프레임마다 손 좌표 21개를 action "landmarks"로 보낸다 (그림판 표시용)


def load_mapping(path=MAPPING_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 1. 좌표 보정 ───────────────────────────────────────────

def to_canvas(v, lo, hi):
    return min(max((v - lo) / (hi - lo), 0.0), 1.0)


def _smoothing(cutoff, dt):
    tau = 1.0 / (2 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """검지 끝 좌표의 떨림을 줄이는 One Euro 필터 (Casiez et al., 2012).

    손이 느리면 세게 보정해 떨림을 없애고, 빠르면 보정을 풀어 늦게 따라오지 않게 한다.
    손이 사라지면 reset해서 다음 획이 튀지 않게 한다.
    """

    def __init__(self, min_cutoff, beta, d_cutoff=1.0, fallback_dt=1 / 15):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.fallback_dt = fallback_dt
        self.reset()

    def reset(self):
        self.pos = None    # 보정된 위치
        self.vel = (0.0, 0.0)
        self.t = None

    def update(self, x, y, t):
        """t: 초. 프레임 간격을 알 수 없으면 fallback_dt로 본다."""
        if self.pos is None:
            self.pos, self.t = (x, y), t
            return self.pos
        dt = t - self.t if t is not None and self.t is not None and t > self.t else self.fallback_dt
        self.t = t
        px, py = self.pos
        a_d = _smoothing(self.d_cutoff, dt)
        vx = a_d * (x - px) / dt + (1 - a_d) * self.vel[0]
        vy = a_d * (y - py) / dt + (1 - a_d) * self.vel[1]
        self.vel = (vx, vy)
        a = _smoothing(self.min_cutoff + self.beta * math.hypot(vx, vy), dt)
        self.pos = (a * x + (1 - a) * px, a * y + (1 - a) * py)
        return self.pos


# ── 2. 동작 판별 ───────────────────────────────────────────

def tip_ratio(lm, tip, pip):
    """손목에서 손가락 끝까지 거리 / 손목에서 가운데 마디까지 거리. 펴질수록 크다."""
    d = lambda a: ((lm[a][0] - lm[WRIST][0]) ** 2 + (lm[a][1] - lm[WRIST][1]) ** 2) ** 0.5
    return d(tip) / max(d(pip), 1e-6)


def extended(lm, tip, pip, ratio):
    return tip_ratio(lm, tip, pip) > ratio


def finger_states(lm, prev, extend_ratio, fold_ratio):
    """손가락 4개의 펴짐. prev(직전 상태)가 있으면 기준 두 개로 기준선 근처의 깜빡임을 막는다."""
    states = []
    for i, (tip, pip) in enumerate(FINGERS):
        r = tip_ratio(lm, tip, pip)
        if prev is None:
            states.append(r > extend_ratio)
        elif prev[i]:
            states.append(r >= fold_ratio)
        else:
            states.append(r > extend_ratio)
    return tuple(states)


def _dist(lm, a, b):
    return math.hypot(lm[a][0] - lm[b][0], lm[a][1] - lm[b][1])


def pinch_gap(lm):
    """엄지 끝–검지 끝 거리 / 손바닥 길이(손목–중지 뿌리). 손 크기·카메라 거리에 상관없는 값."""
    return _dist(lm, THUMB_TIP, INDEX_TIP) / max(_dist(lm, WRIST, MIDDLE_MCP), 1e-6)


def pose_name(fingers, pinched):
    # pinch(OK 사인): 엄지와 검지 끝이 맞닿고 중지·약지·새끼는 편다. 주먹을 쥘 때 엄지가 검지 근처에 오는 것과 구분된다.
    if pinched and all(fingers[1:]):
        return "pinch"
    return POSES.get(fingers, "unknown")


def at_edge(lm, margin):
    """판별에 쓰는 점이 화면 가장자리에 있는지. vision은 화면 밖 점을 0·1로 잘라 보내므로 이런 점은 믿을 수 없다."""
    return any(not (margin <= lm[i][c] <= 1.0 - margin) for i in CLASSIFY_POINTS for c in (0, 1))


def classify(lm, ratio=1.1, pinch=0.3):
    return pose_name(finger_states(lm, None, ratio, ratio), pinch_gap(lm) < pinch)


# ── 3. 동작 → 기능 대응 (세션 하나의 상태) ─────────────────

class Session:
    """PC·휴대폰 한 쌍의 상태. 프레임 하나를 받아 web에 보낼 명령 목록을 돌려준다."""

    def __init__(self, sid, mapping, config):
        self.sid = sid
        self.mapping = mapping
        self.cfg = config
        self.smoother = OneEuroFilter(config.min_cutoff, config.beta)
        self.gesture = None          # 안정화된 현재 동작
        self._candidate = None       # 바뀔 후보 동작과 연속 프레임 수, 처음 보인 시각
        self._count = 0
        self._candidate_since = None
        self._candidate_xy = []      # 후보 동작이 보인 프레임들의 좌표 (확정되면 획 앞부분을 채운다)
        self._fist_since = None      # 주먹이 처음 보인 시각 (ts, ms)
        self._fingers = None         # 직전 손가락 펴짐 상태 (기준 두 개 판별용)
        self._pinched = False        # 직전 엄지–검지 맞닿음 상태
        self._missing = 0            # 손이 연속으로 안 보인 프레임 수
        self._last_gesture = None    # 마지막으로 내보낸 동작 (1회 기능 중복 방지)
        self._stroke = None          # 진행 중인 획 기능 (draw/erase). 바뀌면 up을 보낸다
        self.last_seen = time.monotonic()

    def update(self, frame_id, ts, points, score):
        self.last_seen = time.monotonic()
        if points is None:
            return self._on_no_hand(frame_id, ts)
        self._missing = 0

        if self.cfg.mirror:
            points = [[1.0 - p[0], p[1]] for p in points]
        x, y = points[INDEX_TIP][0], points[INDEX_TIP][1]
        sx, sy = self.smoother.update(x, y, ts / 1000.0)
        xy = (to_canvas(sx, self.cfg.zone_lo, self.cfg.zone_hi),
              to_canvas(sy, self.cfg.zone_lo, self.cfg.zone_hi))

        backfill = self._stabilize(self._classify(points), ts, xy)
        out = []
        if self.gesture is not None:
            gesture = self.gesture
            if gesture == "fist" and ts - self._fist_since >= self.cfg.fist_hold_ms:
                gesture = "fist_hold"
            out = self._emit(gesture, score, frame_id, ts, xy, backfill)
        if self.cfg.send_landmarks:
            out.append(self._landmarks(score, frame_id, ts, points))
        return out

    def _classify(self, points):
        if at_edge(points, self.cfg.edge_margin):
            return "unknown"  # 가장자리에서 잘린 손은 판별하지 않고 직전 동작을 이어 간다
        self._fingers = finger_states(points, self._fingers, self.cfg.extend_ratio, self.cfg.fold_ratio)
        gap = pinch_gap(points)
        self._pinched = gap < (self.cfg.pinch_off if self._pinched else self.cfg.pinch_on)
        return pose_name(self._fingers, self._pinched)

    def _stabilize(self, raw, ts, xy):
        """동작이 새로 확정되면 기다리는 동안의 좌표(이번 프레임 제외)를 돌려준다. 아니면 []."""
        # unknown은 현재 동작을 유지한다. 한 프레임 오인식으로 선이 끊기지 않게 연속 N프레임을 요구한다.
        if raw == "unknown" or raw == self.gesture:
            self._candidate, self._count, self._candidate_xy = None, 0, []
            return []
        if raw == self._candidate:
            self._count += 1
            self._candidate_xy.append(xy)
        else:
            self._candidate, self._count, self._candidate_since = raw, 1, ts
            self._candidate_xy = [xy]
        if self._count < self.cfg.hold_frames:
            return []
        self.gesture = raw
        backfill = self._candidate_xy[:-1]
        self._candidate, self._count, self._candidate_xy = None, 0, []
        if raw == "fist":
            self._fist_since = self._candidate_since  # 확정된 때가 아니라 처음 보인 때부터 센다
        return backfill

    def _on_no_hand(self, frame_id, ts):
        # 1~2프레임 놓친 건 기다리고, 그 이상이면 획을 끝내고 보정·판별 상태를 비운다.
        self._missing += 1
        if self._missing <= self.cfg.lost_grace_frames:
            return []
        out = []
        if self._missing == self.cfg.lost_grace_frames + 1:
            self.smoother.reset()
            self.gesture = None
            self._candidate, self._count, self._candidate_xy = None, 0, []
            self._fingers, self._pinched = None, False
            if self.cfg.send_landmarks:
                out.append(self._landmarks(1.0, frame_id, ts, []))  # 그림판의 손 표시를 지운다
        return self._emit("none", 1.0, frame_id, ts, None) + out

    def _emit(self, gesture, confidence, frame_id, ts, xy, backfill=()):
        action = self.mapping.get(gesture)
        changed = gesture != self._last_gesture
        self._last_gesture = gesture
        out = []

        if self._stroke and action != self._stroke:
            out.append(self._command(gesture, confidence, frame_id, ts, "up"))
            self._stroke = None
            if action == "up":
                return out

        if action is None:
            return out
        if action in CONTINUOUS:
            if action in STROKES and action != self._stroke:
                # 획이 새로 시작되면 확인하느라 기다린 프레임의 좌표부터 이어 그려 앞부분이 잘리지 않게 한다
                out += [self._command(gesture, confidence, frame_id, ts, action, p) for p in backfill]
            out.append(self._command(gesture, confidence, frame_id, ts, action, xy))
            self._stroke = action if action in STROKES else None
        elif changed:
            out.append(self._command(gesture, confidence, frame_id, ts, action))
        return out

    def _landmarks(self, confidence, frame_id, ts, points):
        cmd = self._command(self.gesture or "none", confidence, frame_id, ts, "landmarks")
        cmd["params"] = {
            "points": [[round(p[0], 3), round(p[1], 3)] for p in points],
            "zone": [self.cfg.zone_lo, self.cfg.zone_hi],
        }
        return cmd

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
