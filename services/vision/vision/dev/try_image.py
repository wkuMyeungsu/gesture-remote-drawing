"""개발용: 사진 한 장을 vision의 POST /detect로 보내 손 좌표를 확인한다. stream·gesture 없이 돈다.

    docker compose up --build vision
    python services/vision/dev/try_image.py 손사진.jpg                 # 결과 JSON 출력
    python services/vision/dev/try_image.py 손사진.jpg --draw out.png  # 점을 그린 그림도 저장 (opencv-python 필요)

사진은 휴대폰이 보내는 것처럼 뒤집지 않은 원본을 쓴다. 결과 좌표와 그림은 좌우 반전이 끝난 화면 기준이다.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

# MediaPipe Hands 손가락 연결 (0 손목, 4 엄지 끝, 8 검지 끝, 12 중지 끝, 16 약지 끝, 20 새끼 끝)
LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", help="JPEG 파일")
    p.add_argument("--url", default="http://localhost:8003/detect")
    p.add_argument("--draw", metavar="OUT", help="좌우 반전한 사진에 점을 그려 저장")
    args = p.parse_args()

    with open(args.image, "rb") as f:
        jpeg = f.read()
    req = urllib.request.Request(args.url, data=jpeg, headers={"Content-Type": "image/jpeg"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            result = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"vision 응답 {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"vision에 연결할 수 없음 ({args.url}): {e.reason}")

    hand = result.get("hand")
    if hand is None:
        print(f"손 없음 (처리 {result.get('detect_ms')}ms)")
    else:
        print(f"{hand['handedness']} score={hand['score']} (처리 {result.get('detect_ms')}ms)")
        print(f"  검지 끝(8): {hand['points'][8]}")
    print(json.dumps(result, ensure_ascii=False))

    if args.draw:
        draw(jpeg, hand, args.draw)


def draw(jpeg, hand, out):
    try:
        import cv2
        import numpy as np
    except ImportError:
        sys.exit("--draw에는 opencv-python이 필요하다 (pip install opencv-python)")
    image = cv2.flip(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR), 1)
    h, w = image.shape[:2]
    if hand:
        pts = [(int(x * w), int(y * h)) for x, y, _ in hand["points"]]
        for a, b in LINKS:
            cv2.line(image, pts[a], pts[b], (0, 200, 0), 2)
        for i, pt in enumerate(pts):
            cv2.circle(image, pt, 6 if i == 8 else 3, (0, 0, 255) if i == 8 else (255, 255, 255), -1)
    cv2.imwrite(out, image)
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
