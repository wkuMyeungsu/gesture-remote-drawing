"""security 세션 API (8001). 바깥 요청은 nginx(443)가 받고, 이 서버는 nginx 뒤에서만 쓴다.

연결 (docs/architecture.html, docs/contracts/README.md):
  ②  PC 브라우저 → POST /api/session: sid와 QR용 1회용 token 발급
  ①  휴대폰 → /m/?token=…, /ws/stream: nginx가 auth_request로 GET /auth에 먼저 묻는다
      - /m/?token=… : 토큰을 폐기하고 sid 쿠키(HttpOnly; Secure; SameSite=Strict)를 발급
      - 그 밖에는 sid 쿠키로 확인
      - 통과하면 X-Session-Id 헤더로 세션 ID를 돌려주고, nginx가 그대로 stream에 붙인다
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import load_settings
from .store import SessionStore

log = logging.getLogger("uvicorn.error")
COOKIE = "sid"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.store = SessionStore(settings)
    log.info("security 시작: 토큰 %d초, 세션 %d초, QR 호스트=%s", settings.token_ttl, settings.session_ttl,
             settings.public_host or "(요청의 Host)")
    yield


app = FastAPI(title="security", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    return {"service": "security", "status": "ok", "sessions": len(request.app.state.store.sessions)}


@app.post("/api/session")
async def create_session(request: Request):
    s = request.app.state
    session = s.store.create()
    if session is None:
        return JSONResponse({"error": "too_many_sessions"}, status_code=503)
    host = s.settings.public_host or request.headers.get("host", "localhost")
    return {
        "sid": session.sid,
        "token": session.token,
        "pair_url": f"https://{host}/m/?token={session.token}",
        "expires_in": s.settings.token_ttl,
    }


@app.get("/auth")
async def auth(request: Request):
    """nginx auth_request 전용 (nginx에서 internal). 2xx면 통과, 401이면 막는다."""
    s = request.app.state
    uri = urlsplit(request.headers.get("x-original-uri", ""))
    token = parse_qs(uri.query).get("token", [""])[0]

    # 새 QR을 찍었으면 예전 쿠키보다 토큰이 먼저다
    if token and uri.path.startswith("/m/"):
        session = s.store.redeem(token)
        if session is not None:
            response = Response(status_code=204, headers={"X-Session-Id": session.sid})
            response.set_cookie(COOKIE, session.phone_key, max_age=s.settings.session_ttl, path="/",
                                httponly=True, secure=s.settings.cookie_secure, samesite="strict")
            log.info("휴대폰 페어링: sid=%s", session.sid)
            return response

    # 페이지 새로고침(이미 쓴 토큰이 주소에 남아 있음)과 /ws/stream은 쿠키로 확인
    session = s.store.by_cookie(request.cookies.get(COOKIE, ""))
    if session is not None:
        return Response(status_code=204, headers={"X-Session-Id": session.sid})
    return Response(status_code=401)
