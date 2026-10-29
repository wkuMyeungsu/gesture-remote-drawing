"""stream 테스트. 실제 휴대폰·security·vision·web 없이 TestClient와 가짜 객체로 돈다.

실행 (저장소 루트에서): docker compose run --rm --no-deps stream pytest -q
"""
import asyncio
import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app

# 실제 인코더(Pillow)로 만든 40x30 JPEG
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAeACgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDYooorwT2AooooAKKKKACiiigAooooAKKKKAP/2Q=="
)
B64 = base64.b64encode(JPEG).decode()
SID = "5f0c2a9e-0d7b-4c1e-9a3f-2b8e6d4c1a70"


class FakeServices:
    """vision(POST /frames)과 web(POST /events) 대신 요청을 기록한다."""

    def __init__(self, vision_status=200, vision_delay=0.0):
        self.vision_status, self.vision_delay = vision_status, vision_delay
        self.frames, self.events = [], []

    async def __call__(self, request: httpx.Request):
        body = await request.aread()
        if request.url.path == "/frames":
            self.frames.append({"url": str(request.url), "content_type": request.headers["content-type"], "body": body,
                                "sid": request.headers["x-session-id"], "frame_id": request.headers["x-frame-id"],
                                "ts": int(request.headers["x-ts"])})
            if self.vision_delay:
                await asyncio.sleep(self.vision_delay)
            return httpx.Response(self.vision_status, json={"ok": self.vision_status == 200})
        if request.url.path == "/events":
            self.events.append(json.loads(body))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


@pytest.fixture
def make_client(monkeypatch):
    def make(**env):
        defaults = {"STREAM_DEV_SID": "", "STREAM_VISION_URL": "http://vision:8003/frames", "STREAM_EVENTS_URL": "http://web:8000/events",
                    "GESTURE_ZONE_LO": "", "GESTURE_ZONE_HI": ""}
        for key, value in {**defaults, **env}.items():
            monkeypatch.setenv(key, value)
        return TestClient(app)
    return make


def use_fake(client, fake):
    client.app.state.relay.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))


def phone(client, sid=SID, **kwargs):
    headers = {"X-Session-Id": sid} if sid else {}
    return client.websocket_connect("/ws/stream", headers=headers, **kwargs)


def wait_for(condition, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_health_and_camera_page(make_client):
    with make_client() as c:
        assert c.get("/health").json()["status"] == "ok"
        r = c.get("/m/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "/ws/stream" in r.text
        assert c.get("/m", follow_redirects=False).headers["location"] == "/m/"


def test_no_session_header_is_rejected(make_client):
    """세션은 security가 붙인 X-Session-Id만 믿는다. 없으면 4401로 닫는다."""
    with make_client() as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/stream?sid=fake") as ws:
                ws.receive_json()
        assert e.value.code == 4401


def test_frame_goes_to_vision_and_ack_comes_back(make_client):
    """① 프레임 → ④ vision에 JPEG·헤더로 → vision이 200이면 ack(dropped false). ⑧ 연결·끊김 알림."""
    with make_client() as c:
        fake = FakeServices()
        use_fake(c, fake)
        with phone(c) as ws:
            assert ws.receive_json() == {"type": "config", "width": 320, "height": 240, "quality": 0.6, "max_fps": 15,
                                         "draw_zone": [0.15, 0.15, 0.85, 0.85]}
            before = int(time.time() * 1000)
            ws.send_text(json.dumps({"frame_id": 42, "jpeg": B64}))
            assert ws.receive_json() == {"type": "ack", "frame_id": 42, "dropped": False}
            f = fake.frames[0]
            assert f["url"] == "http://vision:8003/frames" and f["content_type"] == "image/jpeg" and f["body"] == JPEG
            assert f["sid"] == SID and f["frame_id"] == "42" and before <= f["ts"] <= int(time.time() * 1000)
        assert wait_for(lambda: fake.events == [{"sid": SID, "event": "connected"}, {"sid": SID, "event": "disconnected"}])


def test_frame_while_waiting_for_vision_is_dropped(make_client):
    """vision 응답을 기다리는 중에 들어온 프레임은 큐에 쌓지 않고 바로 버린다."""
    with make_client() as c:
        fake = FakeServices(vision_delay=0.1)
        use_fake(c, fake)
        with phone(c) as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 1, "jpeg": B64}))
            ws.send_text(json.dumps({"frame_id": 2, "jpeg": B64}))
            first, second = ws.receive_json(), ws.receive_json()
            assert first == {"type": "ack", "frame_id": 2, "dropped": True, "reason": "busy"}  # 2번이 먼저 버려짐
            assert second == {"type": "ack", "frame_id": 1, "dropped": False}
            assert [f["frame_id"] for f in fake.frames] == ["1"]


@pytest.mark.parametrize("status, delay, reason", [(200, 0.4, "vision_timeout"), (503, 0, "vision_503"), (500, 0, "vision_500")])
def test_vision_timeout_or_error_drops_frame(make_client, status, delay, reason):
    with make_client() as c:
        use_fake(c, FakeServices(vision_status=status, vision_delay=delay))
        with phone(c) as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 7, "jpeg": B64}))
            assert ws.receive_json() == {"type": "ack", "frame_id": 7, "dropped": True, "reason": reason}
        stats = c.get("/sessions").json()[0]
        assert stats["dropped_vision"] == 1 and stats["forwarded"] == 0


def test_vision_unreachable_drops_frame(make_client):
    with make_client(STREAM_VISION_URL="http://127.0.0.1:9/frames") as c:
        with phone(c) as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 3, "jpeg": B64}))
            assert ws.receive_json()["reason"] == "vision_unreachable"


@pytest.mark.parametrize("message, frame_id", [
    ("not json", None),
    (json.dumps({"jpeg": B64}), None),  # frame_id 없음
    (json.dumps({"frame_id": "5", "jpeg": B64}), None),  # 정수가 아님
    (json.dumps({"frame_id": 5, "jpeg": "!!!"}), 5),  # base64가 아님
    (json.dumps({"frame_id": 6, "jpeg": base64.b64encode(b"PNG data").decode()}), 6),  # JPEG가 아님
])
def test_invalid_frames_are_dropped(make_client, message, frame_id):
    with make_client() as c:
        fake = FakeServices()
        use_fake(c, fake)
        with phone(c) as ws:
            ws.receive_json()
            ws.send_text(message)
            assert ws.receive_json() == {"type": "ack", "frame_id": frame_id, "dropped": True, "reason": "invalid_frame"}
        assert fake.frames == []


def test_data_uri_prefix_is_accepted(make_client):
    with make_client() as c:
        use_fake(c, FakeServices())
        with phone(c) as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 1, "jpeg": "data:image/jpeg;base64," + B64}))
            assert ws.receive_json()["dropped"] is False


def test_second_phone_replaces_first(make_client):
    with make_client() as c:
        fake = FakeServices()
        use_fake(c, fake)
        with phone(c) as first:
            first.receive_json()
            with phone(c) as second:
                second.receive_json()
                with pytest.raises(WebSocketDisconnect) as e:
                    first.receive_json()
                assert e.value.code == 4409
        assert wait_for(lambda: [x["event"] for x in fake.events] == ["connected", "disconnected"])  # 바뀔 때는 알리지 않음


def test_dev_sid_without_security(make_client):
    """STREAM_DEV_SID를 정하면 security 없이 ?sid= 또는 그 값으로 테스트할 수 있다."""
    with make_client(STREAM_DEV_SID="dev") as c:
        fake = FakeServices()
        use_fake(c, fake)
        with c.websocket_connect("/ws/stream?sid=my-test") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 0, "jpeg": B64}))
            ws.receive_json()
        with c.websocket_connect("/ws/stream") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 0, "jpeg": B64}))
            ws.receive_json()
        assert [f["sid"] for f in fake.frames] == ["my-test", "dev"]


def test_header_wins_over_query_sid(make_client):
    with make_client(STREAM_DEV_SID="dev") as c:
        fake = FakeServices()
        use_fake(c, fake)
        with c.websocket_connect("/ws/stream?sid=forged", headers={"X-Session-Id": SID}) as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"frame_id": 0, "jpeg": B64}))
            ws.receive_json()
        assert fake.frames[0]["sid"] == SID


def test_recent_window_stats(make_client):
    """/sessions의 recent: 최근 10초 초당 처리 수, vision 응답 시간, 버린 비율."""
    with make_client() as c:
        fake = FakeServices()
        use_fake(c, fake)
        with phone(c) as ws:
            ws.receive_json()
            for i in range(4):
                ws.send_text(json.dumps({"frame_id": i, "jpeg": B64}))
                assert ws.receive_json()["dropped"] is False
            ws.send_text("not json")  # 버린 프레임 1장
            ws.receive_json()
            recent = c.get("/sessions").json()[0]["recent"]
        assert recent["fps"] == 0.4 and recent["drop_rate"] == 0.2
        assert recent["vision_ms_avg"] is not None and recent["vision_ms_p95"] >= recent["vision_ms_avg"] * 0.5


def test_prune_removes_only_old_disconnected_sessions(make_client):
    with make_client() as c:
        use_fake(c, FakeServices())
        relay = c.app.state.relay
        with phone(c, sid="gone") as ws:
            ws.receive_json()
        with phone(c, sid="still-here") as ws:
            ws.receive_json()
            assert relay.prune(ttl=0) == ["gone"]  # 연결 중인 세션은 지우지 않는다
            assert set(relay.sessions) == {"still-here"}


def test_shutdown_tells_web_phones_are_gone():
    """stream이 꺼질 때 연결된 휴대폰을 끊김으로 알린다 (그림판이 '연결됨'으로 남지 않게)."""
    from app.config import load_settings
    from app.relay import Relay

    fake = FakeServices()
    relay = Relay(load_settings(), httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    relay.session("a").phone = object()
    relay.session("b")  # 휴대폰 없음
    asyncio.run(relay.notify_all_disconnected())
    assert fake.events == [{"sid": "a", "event": "disconnected"}]


@pytest.mark.parametrize("lo, hi, expected", [
    ("0.1", "0.9", [0.1, 0.1, 0.9, 0.9]),      # gesture와 같은 .env 값을 따른다
    ("0.9", "0.1", [0.15, 0.15, 0.85, 0.85]),  # 거꾸로면 기본값
    ("abc", "0.8", [0.15, 0.15, 0.85, 0.85]),  # 숫자가 아니면 기본값
])
def test_draw_zone_follows_gesture_env(make_client, lo, hi, expected):
    """카메라 페이지의 그림판 영역은 gesture 좌표 보정 범위(GESTURE_ZONE_LO·HI)와 같아야 한다."""
    with make_client(GESTURE_ZONE_LO=lo, GESTURE_ZONE_HI=hi) as c:
        with phone(c) as ws:
            assert ws.receive_json()["draw_zone"] == expected
