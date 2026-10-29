"""security 세션 API 테스트. nginx 없이 TestClient로 /api/session과 /auth(auth_request 대상)를 확인한다.

실행 (저장소 루트에서): docker compose run --rm --no-deps security pytest -q
"""
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import app
from app.store import SessionStore


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """컨테이너에는 .env의 SECURITY_* 값이 들어 있으므로, 테스트는 기본값에서 시작한다."""
    for name in ("SECURITY_PUBLIC_HOST", "SECURITY_CERT_HOST", "SECURITY_MAX_SESSIONS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client(clock):
    with TestClient(app, base_url="https://192.168.0.12") as c:
        c.app.state.store = SessionStore(load_settings(), clock=clock)
        yield c


def new_session(client):
    r = client.post("/api/session")
    assert r.status_code == 200
    return r.json()


def auth(client, uri, cookie=None):
    client.cookies.clear()
    if cookie:
        client.cookies.set("sid", cookie)
    return client.get("/auth", headers={"X-Original-URI": uri})


def pair(client, body):
    r = auth(client, f"/m/?token={body['token']}")
    assert r.status_code == 204
    return r.cookies["sid"]


def test_health(client):
    assert client.get("/health").json()["service"] == "security"


def test_session_matches_contract(client):
    body = new_session(client)
    assert set(body) == {"sid", "token", "pair_url", "expires_in"}
    assert len(body["sid"]) == 36
    assert body["pair_url"] == f"https://192.168.0.12/m/?token={body['token']}"
    assert body["expires_in"] == 60


def test_public_host_overrides_host_header(client, monkeypatch):
    monkeypatch.setenv("SECURITY_PUBLIC_HOST", "10.0.0.5")
    client.app.state.settings = load_settings()
    assert new_session(client)["pair_url"].startswith("https://10.0.0.5/m/?token=")


def test_token_gives_session_and_cookie(client):
    body = new_session(client)
    r = auth(client, f"/m/?token={body['token']}")
    assert r.status_code == 204
    assert r.headers["x-session-id"] == body["sid"]
    set_cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", "path=/"):
        assert attr in set_cookie
    assert body["sid"] not in r.headers["set-cookie"]  # 쿠키에는 sid가 아니라 휴대폰 전용 키


def test_token_is_single_use(client):
    body = new_session(client)
    pair(client, body)
    assert auth(client, f"/m/?token={body['token']}").status_code == 401  # 다른 휴대폰이 같은 QR을 찍음


def test_token_expires(client, clock):
    body = new_session(client)
    clock.now += 61
    assert auth(client, f"/m/?token={body['token']}").status_code == 401


def test_expired_token_cannot_be_retried(client, clock):
    body = new_session(client)
    clock.now += 61
    auth(client, f"/m/?token={body['token']}")
    clock.now -= 61
    assert auth(client, f"/m/?token={body['token']}").status_code == 401


def test_cookie_opens_stream(client):
    body = new_session(client)
    cookie = pair(client, body)
    r = auth(client, "/ws/stream", cookie)
    assert r.status_code == 204
    assert r.headers["x-session-id"] == body["sid"]
    assert "set-cookie" not in r.headers


def test_reload_with_used_token_uses_cookie(client):
    body = new_session(client)
    cookie = pair(client, body)
    r = auth(client, f"/m/?token={body['token']}", cookie)
    assert r.status_code == 204
    assert r.headers["x-session-id"] == body["sid"]


def test_new_qr_beats_old_cookie(client):
    old = new_session(client)
    cookie = pair(client, old)
    new = new_session(client)
    r = auth(client, f"/m/?token={new['token']}", cookie)
    assert r.headers["x-session-id"] == new["sid"]


def test_token_only_works_on_camera_page(client):
    body = new_session(client)
    assert auth(client, f"/ws/stream?token={body['token']}").status_code == 401
    assert auth(client, f"/m/?token={body['token']}").status_code == 204  # 위 요청이 토큰을 쓰지 않았다


@pytest.mark.parametrize("cookie", [None, "", "forged"])
def test_no_or_bad_cookie_is_rejected(client, cookie):
    assert auth(client, "/ws/stream", cookie).status_code == 401


def test_sid_as_cookie_is_rejected(client):
    body = new_session(client)
    pair(client, body)
    assert auth(client, "/ws/stream", body["sid"]).status_code == 401  # PC가 아는 sid로 휴대폰 흉내


def test_session_expires(client, clock):
    body = new_session(client)
    cookie = pair(client, body)
    clock.now += 3 * 60 * 60 + 1
    assert auth(client, "/ws/stream", cookie).status_code == 401
    new_session(client)  # 발급 때 만료 세션 정리
    assert body["sid"] not in client.app.state.store.sessions


def test_session_limit(client, monkeypatch):
    monkeypatch.setenv("SECURITY_MAX_SESSIONS", "2")
    client.app.state.store = SessionStore(load_settings())
    new_session(client)
    new_session(client)
    assert client.post("/api/session").status_code == 503


def test_cert_host_is_used_for_qr(client, monkeypatch):
    monkeypatch.setenv("SECURITY_CERT_HOST", "192.168.0.77")
    client.app.state.settings = load_settings()
    r = client.post("/api/session", headers={"Host": "localhost"})  # PC 브라우저를 localhost로 열어도
    assert r.json()["pair_url"].startswith("https://192.168.0.77/m/?token=")
