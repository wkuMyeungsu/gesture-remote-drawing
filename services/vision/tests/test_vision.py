"""vision 테스트. stream·gesture 없이 TestClient와 가짜 객체로 돈다.

실행 (저장소 루트에서): docker compose run --rm --no-deps vision pytest -q
"""
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import load_settings
from app.detector import ANCHOR, BadImage, HandDetector, SessionDetectors, decode, pick_hand, preprocess

SID = "5f0c2a9e-0d7b-4c1e-9a3f-2b8e6d4c1a70"
HEADERS = {"Content-Type": "image/jpeg", "X-Session-Id": SID, "X-Frame-Id": "42", "X-Ts": "1793253600123"}


def make_jpeg(w=320, h=240, color=(40, 40, 40)) -> bytes:
    image = np.full((h, w, 3), color, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return buf.tobytes()


JPEG = make_jpeg()


# ---------- MediaPipe 결과 흉내 ----------

def fake_hand(label, score, x0=0.5, y0=0.6):
    marks = SimpleNamespace(landmark=[SimpleNamespace(x=x0 + i * 0.01, y=y0, z=-0.01 * i) for i in range(21)])
    hand = SimpleNamespace(classification=[SimpleNamespace(label=label, score=score)])
    return marks, hand


def fake_results(*hands):
    return SimpleNamespace(multi_hand_landmarks=[m for m, _ in hands] or None,
                           multi_handedness=[h for _, h in hands] or None)


def test_pick_hand_none():
    assert pick_hand(fake_results(), mirrored=True) is None
    assert pick_hand(SimpleNamespace(multi_hand_landmarks=None, multi_handedness=None), mirrored=True) is None


def test_pick_hand_highest_score_and_format():
    hand = pick_hand(fake_results(fake_hand("Left", 0.7), fake_hand("Right", 0.94)), mirrored=True)
    assert hand["handedness"] == "Right"
    assert hand["score"] == 0.94
    assert len(hand["points"]) == 21
    assert all(len(p) == 3 for p in hand["points"])
    assert hand["points"][0] == [0.5, 0.6, 0.0]


def test_pick_hand_swaps_label_when_not_mirrored():
    # MediaPipe는 셀카(반전된) 이미지를 가정하므로 반전하지 않았으면 왼손·오른손을 바꾼다
    assert pick_hand(fake_results(fake_hand("Left", 0.9)), mirrored=False)["handedness"] == "Right"


def test_pick_hand_clamps_xy():
    hand = pick_hand(fake_results(fake_hand("Right", 0.9, x0=0.99)), mirrored=True)
    xs = [p[0] for p in hand["points"]]
    assert max(xs) == 1.0 and min(xs) == 0.99


def test_pick_hand_keeps_same_hand_as_previous_frame():
    # 손이 둘이면 score가 아니라 앞 프레임 손과 가까운 쪽을 고른다 (두 손 사이를 오가며 좌표가 튀지 않게)
    left = fake_hand("Left", 0.95, x0=0.1, y0=0.2)
    right = fake_hand("Right", 0.80, x0=0.6, y0=0.7)
    prev = (0.6 + ANCHOR * 0.01, 0.7)
    hand = pick_hand(fake_results(left, right), mirrored=True, prev=prev)
    assert hand["handedness"] == "Right" and hand["points"][0][0] == 0.6
    # 앞 프레임 손이 없으면 score로
    assert pick_hand(fake_results(left, right), mirrored=True)["handedness"] == "Left"


# ---------- 세션별 추적기 ----------

class FakeTracker:
    made = []

    def __init__(self):
        self.frames, self.closed = 0, False
        FakeTracker.made.append(self)

    def detect(self, jpeg, ts=None):
        self.frames += 1
        return None

    def close(self):
        self.closed = True


def test_session_detectors_one_tracker_per_session(monkeypatch):
    monkeypatch.setattr(FakeTracker, "made", [])
    pool = SessionDetectors(load_settings(), FakeTracker)
    pool.detect("a", JPEG)
    pool.detect("a", JPEG)
    pool.detect("b", JPEG)
    assert len(pool) == 2 and len(FakeTracker.made) == 2
    assert [t.frames for t in FakeTracker.made] == [2, 1]
    pool.close()
    assert all(t.closed for t in FakeTracker.made) and len(pool) == 0


def test_session_detectors_evicts_oldest_and_idle(monkeypatch):
    monkeypatch.setattr(FakeTracker, "made", [])
    monkeypatch.setenv("VISION_MAX_SESSIONS", "2")
    monkeypatch.setenv("VISION_SESSION_IDLE_S", "1000")
    pool = SessionDetectors(load_settings(), FakeTracker)
    for sid in ("a", "b", "a", "c"):  # c가 들어오면 가장 오래 쉰 b를 정리
        pool.detect(sid, JPEG)
    assert len(pool) == 2 and FakeTracker.made[1].closed and not FakeTracker.made[0].closed

    monkeypatch.setenv("VISION_SESSION_IDLE_S", "0")
    pool.settings = load_settings()
    pool.detect("d", JPEG)  # 쉰 세션(a, c)은 정리
    assert len(pool) == 1
    pool.close()


class FakeHands:
    """mp.solutions.hands.Hands 흉내: 손 하나를 돌려주고 reset 횟수를 센다."""

    def __init__(self):
        self.resets = 0

    def process(self, rgb):
        return fake_results(fake_hand("Right", 0.9))

    def reset(self):
        self.resets += 1


def tracking_detector(monkeypatch, gap_ms="1000"):
    monkeypatch.setenv("VISION_RESET_GAP_MS", gap_ms)
    det = HandDetector.__new__(HandDetector)  # MediaPipe 없이 HandDetector의 추적 로직만 확인
    det.settings, det.static, det._hands = load_settings(), False, FakeHands()
    det._lock, det._prev, det._last_ts, det.resets = __import__("threading").Lock(), None, None, 0
    return det


def test_tracking_resets_after_time_gap(monkeypatch):
    det = tracking_detector(monkeypatch)
    for ts in (1000, 1100, 1200):  # 100ms 간격: 이어서 추적
        det.detect(JPEG, ts)
    assert det._hands.resets == 0 and det._prev is not None
    det.detect(JPEG, 5000)  # 3.8초 끊김: 처음부터
    assert det._hands.resets == 1 and det.resets == 1
    det.detect(JPEG, 4000)  # 시간이 거꾸로 감(새 연결 등): 처음부터
    assert det._hands.resets == 2
    det.detect(JPEG, None)  # ts 없음: 판단하지 않음
    assert det._hands.resets == 2


def test_tracking_reset_disabled(monkeypatch):
    det = tracking_detector(monkeypatch, gap_ms="0")
    det.detect(JPEG, 1000)
    det.detect(JPEG, 99999)
    assert det._hands.resets == 0


# ---------- 전처리 ----------

def test_decode_rejects_non_jpeg():
    with pytest.raises(BadImage):
        decode(b"not a jpeg")
    with pytest.raises(BadImage):
        decode(b"\xff\xd8" + b"\x00" * 100)


def test_preprocess_mirrors_and_converts_to_rgb(monkeypatch):
    monkeypatch.setenv("VISION_MIRROR", "1")
    image = np.zeros((10, 20, 3), np.uint8)
    image[:, :5] = (255, 0, 0)  # 왼쪽 띠, BGR의 파랑
    out = preprocess(image, load_settings())
    assert out.shape == (10, 20, 3)
    assert tuple(out[0, -1]) == (0, 0, 255)  # 오른쪽 끝으로 옮겨지고 RGB의 파랑
    assert tuple(out[0, 0]) == (0, 0, 0)


def test_preprocess_without_mirror(monkeypatch):
    monkeypatch.setenv("VISION_MIRROR", "0")
    image = np.zeros((10, 20, 3), np.uint8)
    image[:, :5] = (255, 0, 0)
    assert tuple(preprocess(image, load_settings())[0, 0]) == (0, 0, 255)


def test_preprocess_downscales_large_frames(monkeypatch):
    monkeypatch.setenv("VISION_MAX_SIDE", "320")
    out = preprocess(np.zeros((960, 1280, 3), np.uint8), load_settings())
    assert out.shape == (240, 320, 3)


# ---------- HTTP ----------

class FakeDetector:
    """MediaPipe 대신 정해진 손을 돌려준다. JPEG 확인은 진짜 decode로 한다."""

    hand = {"handedness": "Right", "score": 0.94, "points": [[0.5, 0.6, 0.0]] * 21}

    def __init__(self, settings, static=True):
        self.calls = 0

    def detect(self, jpeg, ts=None):
        decode(jpeg)
        self.calls += 1
        return FakeDetector.hand

    def close(self):
        pass


class FakeGesture:
    def __init__(self, status=200, error=None):
        self.status, self.error = status, error
        self.received = []

    def __call__(self, request: httpx.Request):
        import json
        if self.error:
            raise self.error
        self.received.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(self.status, json={"ok": self.status == 200})


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(main, "HandDetector", FakeDetector)
    monkeypatch.setattr(FakeDetector, "hand", FakeDetector.hand)
    clients = []

    def make(fake=None, **env):
        defaults = {"VISION_GESTURE_URL": "http://gesture:8004/landmarks"}
        for key, value in {**defaults, **env}.items():
            monkeypatch.setenv(key, value)
        client = TestClient(main.app)
        client.__enter__()  # lifespan 실행 (설정·detector 준비)
        clients.append(client)
        if fake is not None:
            client.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def test_health(make_client):
    client = make_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "vision"


def test_frame_is_sent_to_gesture(make_client):
    fake = FakeGesture()
    client = make_client(fake)
    r = client.post("/frames", content=JPEG, headers=HEADERS)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(fake.received) == 1
    sent = fake.received[0]
    assert sent["url"] == "http://gesture:8004/landmarks"
    assert sent["body"] == {"sid": SID, "frame_id": 42, "ts": 1793253600123, "hand": FakeDetector.hand}
    stats = client.get("/stats").json()
    assert stats["received"] == 1 and stats["hands"] == 1 and stats["sent"] == 1


def test_no_hand_sends_null(make_client, monkeypatch):
    monkeypatch.setattr(FakeDetector, "hand", None)
    fake = FakeGesture()
    client = make_client(fake)
    assert client.post("/frames", content=JPEG, headers=HEADERS).status_code == 200
    assert fake.received[0]["body"]["hand"] is None
    assert client.get("/stats").json()["no_hand"] == 1


@pytest.mark.parametrize("drop", ["X-Session-Id", "X-Frame-Id", "X-Ts"])
def test_missing_header_is_400(make_client, drop):
    fake = FakeGesture()
    client = make_client(fake)
    headers = {k: v for k, v in HEADERS.items() if k != drop}
    r = client.post("/frames", content=JPEG, headers=headers)
    assert r.status_code == 400 and r.json()["ok"] is False
    assert fake.received == []


def test_bad_frame_id_is_400(make_client):
    client = make_client(FakeGesture())
    assert client.post("/frames", content=JPEG, headers={**HEADERS, "X-Frame-Id": "abc"}).status_code == 400


def test_bad_jpeg_is_400(make_client):
    fake = FakeGesture()
    client = make_client(fake)
    assert client.post("/frames", content=b"hello", headers=HEADERS).status_code == 400
    assert client.post("/frames", content=b"", headers=HEADERS).status_code == 400
    assert fake.received == []
    assert client.get("/stats").json()["invalid"] == 2


def test_too_large_is_400(make_client):
    client = make_client(FakeGesture(), VISION_MAX_FRAME_BYTES="100")
    assert client.post("/frames", content=JPEG, headers=HEADERS).status_code == 400


def test_gesture_error_is_502(make_client):
    client = make_client(FakeGesture(status=500))
    r = client.post("/frames", content=JPEG, headers=HEADERS)
    assert r.status_code == 502
    assert "gesture 응답 500" in r.json()["error"]


def test_gesture_unreachable_is_502(make_client):
    client = make_client(FakeGesture(error=httpx.ConnectError("refused")))
    r = client.post("/frames", content=JPEG, headers=HEADERS)
    assert r.status_code == 502
    assert client.get("/stats").json()["send_failed"] == 1


def test_gesture_disabled(make_client):
    fake = FakeGesture()
    client = make_client(fake, VISION_GESTURE_URL="")
    assert client.post("/frames", content=JPEG, headers=HEADERS).json() == {"ok": True}
    assert fake.received == []


def test_frames_use_session_tracker(make_client):
    client = make_client(FakeGesture())
    other = {**HEADERS, "X-Session-Id": "other"}
    for headers in (HEADERS, HEADERS, other):
        assert client.post("/frames", content=JPEG, headers=headers).status_code == 200
    stats = client.get("/stats").json()
    assert stats["tracked_sessions"] == 2 and stats["tracking_resets"] == 0
    assert client.app.state.detector.calls == 0  # 사진 한 장용 detector는 쓰지 않음


def test_frames_without_tracking(make_client):
    client = make_client(FakeGesture(), VISION_TRACKING="0")
    assert client.post("/frames", content=JPEG, headers=HEADERS).status_code == 200
    assert client.app.state.detector.calls == 1
    assert client.get("/stats").json()["tracked_sessions"] is None


def test_detect_endpoint(make_client):
    client = make_client(FakeGesture())
    r = client.post("/detect", content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    assert r.json()["hand"] == FakeDetector.hand
    assert client.post("/detect", content=b"nope").status_code == 400


# ---------- 실제 MediaPipe ----------

def test_real_mediapipe_blank_frame_has_no_hand(monkeypatch):
    pytest.importorskip("mediapipe")
    from app.detector import HandDetector

    for static in (True, False):
        detector = HandDetector(load_settings(), static=static)
        try:
            assert detector.detect(JPEG, 1000) is None
            assert detector.detect(JPEG, 9000) is None  # 추적 모드면 간격이 벌어져 reset()까지 돈다
            assert detector.resets == (0 if static else 1)
        finally:
            detector.close()
