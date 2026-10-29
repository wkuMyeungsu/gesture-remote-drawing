"""vision 대신 stream의 ④ 프레임을 받는 테스트 도구 (표준 라이브러리만 사용).

    python services/stream/dev/fake_vision.py                      # 8003에서 받고 30ms 뒤 {"ok": true}
    python services/stream/dev/fake_vision.py --delay 0.3          # 느린 vision 흉내 (stream 200ms 시간 제한 확인)
    python services/stream/dev/fake_vision.py --landmarks http://localhost:8004/landmarks   # gesture에 ⑤ hand: null도 보냄

compose의 vision 컨테이너 대신 PC에서 띄운다 (8003 포트가 겹치므로 vision 컨테이너는 끈다).
컨테이너 안의 stream이 PC의 이 도구를 부르도록 루트 .env에 STREAM_VISION_URL=http://host.docker.internal:8003/frames 를 넣는다.
1초마다 받은 장수·크기·지연을 출력한다.
"""
import argparse
import json
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

stats = {"frames": 0, "bytes": 0, "size": None, "lag_ms": [], "sids": set()}
lock = threading.Lock()


def jpeg_size(data: bytes):
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def make_handler(delay: float, landmarks_url: str | None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._json(200, {"service": "fake-vision", "status": "ok"})

        def do_POST(self):
            if self.path != "/frames":
                return self._json(404, {"error": "not found"})
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            sid, frame_id, ts = self.headers.get("X-Session-Id"), self.headers.get("X-Frame-Id"), self.headers.get("X-Ts")
            if body[:2] != b"\xff\xd8" or not sid or frame_id is None or ts is None:
                return self._json(400, {"error": "JPEG 본문과 X-Session-Id, X-Frame-Id, X-Ts 헤더가 필요합니다"})
            with lock:
                stats["frames"] += 1
                stats["bytes"] += len(body)
                stats["size"] = jpeg_size(body)
                stats["lag_ms"].append(int(time.time() * 1000) - int(ts))
                stats["sids"].add(sid)
            time.sleep(delay)  # MediaPipe 처리 시간 흉내
            if landmarks_url:  # ⑤ 손이 없다고 gesture에 알린다
                payload = {"sid": sid, "frame_id": int(frame_id), "ts": int(ts), "hand": None}
                try:
                    req = urllib.request.Request(landmarks_url, data=json.dumps(payload).encode(),
                                                 headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=0.2).read()
                except Exception as e:
                    print(f"  gesture 전송 실패: {e}")
            self._json(200, {"ok": True})

        def _json(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def report():
    while True:
        time.sleep(1)
        with lock:
            n, size, lags, sids = stats["frames"], stats["size"], stats["lag_ms"], sorted(stats["sids"])
            kb = stats["bytes"] / max(n, 1) / 1024
            stats.update(frames=0, bytes=0, lag_ms=[])
        if n:
            lag = sum(lags) / len(lags)
            shape = f"{size[0]}×{size[1]}" if size else "크기 모름"
            print(f"초당 {n}장, {shape}, 평균 {kb:.1f}KB, stream이 받은 뒤 {lag:.0f}ms 만에 도착, 세션 {sids}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--delay", type=float, default=0.03, help="응답 전에 기다릴 시간 (초)")
    parser.add_argument("--landmarks", help="⑤ hand: null을 보낼 gesture 주소 (예: http://localhost:8004/landmarks)")
    args = parser.parse_args()
    threading.Thread(target=report, daemon=True).start()
    print(f"fake vision: http://0.0.0.0:{args.port}/frames (응답 {args.delay * 1000:.0f}ms 뒤)")
    ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.delay, args.landmarks)).serve_forever()


if __name__ == "__main__":
    main()
