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
from app.detector import BadImage, decode, pick_hand, preprocess

SID = "5f0c2a9e-0d7b-4c1e-9a3f-2b8e6d4c1a70"
HEADERS = {"Content-Type": "image/jpeg", "X-Session-Id": SID, "X-Frame-Id": "42", "X-Ts": "1793253600123"}


def make_jpeg(w=320, h=240, color=(40, 40, 40)) -> bytes:
    image = np.full((h, w, 3), color, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return buf.tobytes()


JPEG = make_jpeg()


# ---------- MediaPipe 결과 흉내 ----------

def fake_hand(label, score, x0=0.5):
    marks = SimpleNamespace(landmark=[SimpleNamespace(x=x0 + i * 0.01, y=0.6, z=-0.01 * i) for i in range(21)])
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

    def __init__(self, settings):
        self.calls = 0

    def detect(self, jpeg):
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

    detector = HandDetector(load_settings())
    try:
        assert detector.detect(JPEG) is None
    finally:
        detector.close()
