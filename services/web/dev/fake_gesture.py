"""gesture 대신 web에 ⑧ 이벤트와 ⑥ 기능 요청을 보내는 테스트 도구 (표준 라이브러리만 사용).

    python services/web/dev/fake_gesture.py --sid <PC 화면에 보이는 세션>

원을 하나 그리고, 색·굵기를 바꿔 원을 하나 더 그린 뒤 마지막 획을 취소한다.
"""
import argparse
import json
import math
import time
import urllib.request


def post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=1) as res:
        return json.loads(res.read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sid", required=True)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--fps", type=float, default=15)
    args = p.parse_args()

    frame_id = 0

    def action(name, **fields):
        nonlocal frame_id
        frame_id += 1
        body = {"sid": args.sid, "frame_id": frame_id, "ts": int(time.time() * 1000),
                "gesture": f"fake_{name}", "confidence": 1.0, "action": name, **fields}
        res = post(args.url, "/actions", body)
        print(name, fields or "", res)
        time.sleep(1 / args.fps)

    def circle(cx, cy, r, steps=40):
        action("hover", x=cx + r, y=cy)
        for i in range(steps + 1):
            t = 2 * math.pi * i / steps
            action("draw", x=round(cx + r * math.cos(t), 4), y=round(cy + r * math.sin(t), 4))
        action("up")

    print("events", post(args.url, "/events", {"sid": args.sid, "event": "connected"}))
    circle(0.35, 0.5, 0.15)
    action("next_color")
    action("size_up")
    action("size_up")
    circle(0.65, 0.5, 0.15)
    time.sleep(1)
    action("undo")


if __name__ == "__main__":
    main()
