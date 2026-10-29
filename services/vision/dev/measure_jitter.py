"""개발용: 휴대폰으로 찍은 손 영상 하나로 좌표 떨림·튐을 숫자로 잰다. 추적 켬/끔을 같은 영상으로 비교한다.

stream·gesture 없이 vision 코드(app.detector)를 직접 부른다. 프레임은 stream처럼 JPEG로 만들어 넣는다.

    # 저장소 루트에서, 컨테이너 안에서 (영상은 services/vision/dev/ 아래에 둔다)
    docker compose run --rm --no-deps -v ./services/vision/dev:/app/dev vision python dev/measure_jitter.py dev/손영상.mp4

    # mediapipe가 설치된 로컬 파이썬이면 services/vision 폴더에서
    python dev/measure_jitter.py 손영상.mp4 --fps 10

잘 재는 법: 손을 카메라 앞에 두고 검지를 펴서 2~3초 가만히 → 천천히 그리기 → 빠르게 그리기를 한 영상에 담는다.
가만히 있는 구간의 "떨림"과 전 구간의 "튐"이 각각 줄었는지 본다.

나오는 값 (검지 끝 8번 점, 좌표는 0~1이고 x는 화면 비율대로 고쳐서 잰다):
  손 찾음   손을 찾은 프레임 비율
  이동 중앙값·p95   연속 두 프레임 사이 검지 끝 이동 거리 (가만히 있을 때 작을수록 덜 떨림)
  튐        이동이 --jump(기본 0.1, 화면 폭의 10%)보다 큰 횟수 (실제로 그렇게 빨리 움직이지 않았다면 오류)
  손 바뀜   보낸 손의 왼손·오른손 라벨이 바뀐 횟수 (한 손만 썼다면 0이어야 함)
  처리      프레임 한 장 평균 처리 시간
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # app 패키지

import cv2  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.detector import HandDetector  # noqa: E402

TIP = 8  # 검지 끝


def read_frames(path: str, fps: float, size: tuple[int, int] | None):
    """영상에서 fps에 맞춰 프레임을 골라 (ts_ms, JPEG 바이트)로 돌려준다. stream처럼 size로 줄여 보낼 수 있다."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"영상을 열 수 없음: {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src_fps / fps)) if fps > 0 else 1
    frames, shape, i = [], None, 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        if i % step == 0:
            if size:
                image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                frames.append((round(i * 1000 / src_fps), buf.tobytes()))
                shape = image.shape[:2]
        i += 1
    cap.release()
    if not frames:
        sys.exit("프레임이 없음")
    return frames, shape


def measure(frames, aspect: float, static: bool, jump: float) -> dict:
    detector = HandDetector(load_settings(), static=static)
    moves, jumps, swaps, found, ms = [], 0, 0, 0, []
    prev_tip, prev_label = None, None
    try:
        for ts, jpeg in frames:
            started = time.perf_counter()
            hand = detector.detect(jpeg, ts)
            ms.append((time.perf_counter() - started) * 1000)
            if hand is None:
                prev_tip = None  # 손이 사라졌다 나타난 건 튐으로 치지 않는다
                continue
            found += 1
            x, y = hand["points"][TIP][:2]
            tip = (x * aspect, y)  # 가로세로 비율을 맞춰 거리 계산
            if prev_tip is not None:
                d = math.dist(tip, prev_tip)
                moves.append(d)
                jumps += d > jump
            if prev_label is not None and hand["handedness"] != prev_label:
                swaps += 1
            prev_tip, prev_label = tip, hand["handedness"]
    finally:
        detector.close()
    q = statistics.quantiles(moves, n=20) if len(moves) >= 2 else [float("nan")] * 19
    return {
        "found": found / len(frames),
        "median": statistics.median(moves) if moves else float("nan"),
        "p95": q[18],
        "jumps": jumps,
        "swaps": swaps,
        "ms": statistics.mean(ms),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", help="손을 찍은 영상 (mp4 등)")
    p.add_argument("--fps", type=float, default=10, help="stream이 보내는 것처럼 초당 이만큼만 고른다 (기본 10)")
    p.add_argument("--size", default="320x240", help="stream처럼 이 크기로 줄여 넣는다 (기본 320x240, 0이면 원본)")
    p.add_argument("--jump", type=float, default=0.1, help="튐으로 셀 한 프레임 이동 거리 (기본 0.1)")
    args = p.parse_args()

    size = None if args.size == "0" else tuple(int(v) for v in args.size.lower().split("x"))
    frames, (h, w) = read_frames(args.video, args.fps, size)
    print(f"{args.video}: 프레임 {len(frames)}장 ({w}×{h}, 초당 {args.fps:g}장)\n")

    rows = [("추적 끔 (예전)", measure(frames, w / h, True, args.jump)),
            ("추적 켬 (지금)", measure(frames, w / h, False, args.jump))]
    print(f"{'':16}{'손 찾음':>8}{'이동 중앙값':>12}{'이동 p95':>10}{'튐':>6}{'손 바뀜':>8}{'처리':>9}")
    for name, r in rows:
        print(f"{name:16}{r['found']:>8.0%}{r['median']:>12.4f}{r['p95']:>10.4f}{r['jumps']:>6}{r['swaps']:>8}"
              f"{r['ms']:>7.1f}ms")
    old, new = rows[0][1], rows[1][1]
    if old["median"] and not math.isnan(old["median"]) and not math.isnan(new["median"]):
        print(f"\n이동 중앙값 {1 - new['median'] / old['median']:+.0%} 감소, 튐 {old['jumps']} → {new['jumps']}")


if __name__ == "__main__":
    main()
