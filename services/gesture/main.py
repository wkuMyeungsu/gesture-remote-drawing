import http.client
import json
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import logic

SERVICE = os.environ.get("SERVICE_NAME", "unknown")
PORT = int(os.environ.get("PORT", "8000"))
WEB_URL = os.environ.get("WEB_URL", "http://web:8000")
TIMEOUT = 0.2  # docs/contracts: 내부 요청 타임아웃 200ms


def config_from_env():
    c = logic.Config()
    c.mirror = os.environ.get("GESTURE_MIRROR", "0") == "1"
    c.alpha = float(os.environ.get("GESTURE_ALPHA", c.alpha))
    c.hold_frames = int(os.environ.get("GESTURE_HOLD_FRAMES", c.hold_frames))
    c.fist_hold_ms = int(os.environ.get("GESTURE_FIST_HOLD_MS", c.fist_hold_ms))
    return c


class WebSender:
    """web의 POST /actions로 명령을 보낸다. 연결 하나를 재사용하고, 큐로 순서를 지키며 vision 응답을 막지 않는다."""

    def __init__(self, base_url):
        u = urlsplit(base_url)
        self.host, self.port = u.hostname, u.port or 80
        self.queue = queue.Queue(maxsize=256)
        self.conn = None
        self.healthy = True
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, command):
        try:
            self.queue.put_nowait(command)
        except queue.Full:
            pass  # web이 막혀 있으면 오래된 명령을 쌓아 두지 않고 버린다

    def _run(self):
        while True:
            body = json.dumps(self.queue.get()).encode()
            ok = self._post(body)
            if ok != self.healthy:
                self.healthy = ok
                print(f"[gesture] web {'reachable' if ok else 'unreachable'} ({self.host}:{self.port})", file=sys.stderr)

    def _post(self, body):
        # 재사용하던 연결이 끊겨 있을 수 있으니 한 번은 새 연결로 다시 시도한다.
        for _ in range(2):
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT)
            try:
                self.conn.request("POST", "/actions", body, {"Content-Type": "application/json"})
                resp = self.conn.getresponse()
                resp.read()
                if resp.will_close:
                    self._close()
                return resp.status == 200
            except (OSError, http.client.HTTPException):
                self._close()
        return False

    def _close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


SESSIONS = logic.SessionStore(config=config_from_env())
SENDER = WebSender(WEB_URL)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # vision이 연결을 재사용할 수 있게

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._reply(200, {"service": SERVICE, "status": "ok"})
        else:
            self._reply(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path.split("?")[0] != "/landmarks":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        try:
            commands = SESSIONS.process(json.loads(raw))
        except ValueError as e:
            self._reply(400, {"ok": False, "error": str(e)})
            return
        for command in commands:
            SENDER.send(command)
        self._reply(200, {"ok": True})

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 초당 15~20번 요청이 오므로 접속 로그는 끈다


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
