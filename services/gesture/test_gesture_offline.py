"""gesture 로직 오프라인 테스트.

  python test_gesture_offline.py                  합성 손 좌표로 시나리오 검사
  python test_gesture_offline.py recording.jsonl  녹화한 ⑤ 메시지(JSON 배열 또는 한 줄에 하나)를 넣고 명령 출력
"""
import json
import sys

import logic

FPS = 15
FRAME_MS = 1000 // FPS


def fake_hand(pose, dx=0.0, dy=0.0, index_ratio=None, pinch=False):
    """손목 (0.5, 0.8) 기준으로 위를 향한 손 21점을 만든다. pose = 펴진 손가락 (검지, 중지, 약지, 새끼).

    index_ratio를 주면 검지 끝을 (손목→끝 거리) / (손목→가운데 마디 거리)가 그 값이 되게 놓는다.
    pinch=True면 엄지 끝을 검지 끝 바로 옆에 놓는다.
    vision처럼 화면 밖 점은 0·1로 자른다.
    """
    wx, wy = 0.5 + dx, 0.8 + dy
    pts = [[wx, wy, 0.0]]
    pts += [[wx - 0.05 * i, wy - 0.03 * i, 0.0] for i in range(1, 5)]  # 엄지 (판별에 안 씀)
    for (ox, up) in zip((-0.04, 0.0, 0.04, 0.08), pose):
        heights = (0.15, 0.22, 0.27, 0.31) if up else (0.15, 0.20, 0.16, 0.12)  # MCP, PIP, DIP, TIP
        pts += [[wx + ox, wy - h, 0.0] for h in heights]
    if index_ratio is not None:
        px, py = pts[6][0] - wx, pts[6][1] - wy
        pts[8] = [wx + px * index_ratio, wy + py * index_ratio, 0.0]
    if pinch:
        pts[4] = [pts[8][0] - 0.01, pts[8][1] + 0.01, 0.0]
    return [[min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0), z] for x, y, z in pts]


POSE = {
    "index_up": (True, False, False, False),
    "two_fingers": (True, True, False, False),
    "open_palm": (True, True, True, True),
    "fist": (False, False, False, False),
    "unknown": (False, True, False, True),
    "pinch": (False, True, True, True),
}


class Runner:
    def __init__(self):
        self.store = logic.SessionStore(config=logic.Config())
        self.frame = 0

    def feed(self, pose, n=1, dx=0.0, dy=0.0, **kw):
        out = []
        for _ in range(n):
            kw_ = {"pinch": True, **kw} if pose == "pinch" else kw
            hand = None if pose is None else {"handedness": "Right", "score": 0.9, "points": fake_hand(POSE[pose], dx, dy, **kw_)}
            msg = {"sid": "test", "frame_id": self.frame, "ts": 1_000_000 + self.frame * FRAME_MS, "hand": hand}
            out += self.store.process(msg)
            self.frame += 1
        return out


def actions(cmds):
    return [c["action"] for c in cmds]


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    return cond


def run_scenarios():
    results = []

    # 1. 포즈 판별
    for name, pose in POSE.items():
        got = logic.classify(fake_hand(pose, pinch=name == "pinch"))
        results.append(check(f"classify {name}", got == name, got))

    # 2. 검지를 들면 hold_frames 뒤부터 draw, 좌표는 0~1이고 좌우 반전됨
    r = Runner()
    first = r.feed("index_up", 3)
    cmds = r.feed("index_up", 5, dx=0.2)
    results.append(check("no output before gesture is stable", first == [], actions(first)))
    results.append(check("draw every frame, stroke start backfilled", actions(cmds) == ["draw"] * 8, actions(cmds)))
    results.append(check("coordinates within 0..1", all(0 <= c["x"] <= 1 and 0 <= c["y"] <= 1 for c in cmds)))
    results.append(check("not mirrored by default: hand moves right -> x goes right", cmds[-1]["x"] > cmds[0]["x"], [c["x"] for c in cmds]))
    mirrored = logic.Session("m", logic.load_mapping(), logic.Config(mirror=True))
    plain = logic.Session("p", logic.load_mapping(), logic.Config())
    pts = fake_hand(POSE["index_up"], dx=0.2)
    m_xy = [mirrored.update(i, i * FRAME_MS, pts, 0.9) for i in range(5)][-1][0]
    p_xy = [plain.update(i, i * FRAME_MS, pts, 0.9) for i in range(5)][-1][0]
    results.append(check("GESTURE_MIRROR flips x", abs(m_xy["x"] - logic.to_canvas(1 - (0.7 - 0.04), 0.15, 0.85)) < 1e-3
                         and abs(p_xy["x"] - logic.to_canvas(0.7 - 0.04, 0.15, 0.85)) < 1e-3, (m_xy, p_xy)))

    # 3. 한 프레임 오인식은 선을 끊지 않는다
    cmds = r.feed("open_palm", 1) + r.feed("unknown", 1) + r.feed("index_up", 2)
    results.append(check("single misread keeps drawing", actions(cmds) == ["draw"] * 4, actions(cmds)))

    # 4. 손을 1~2프레임 놓쳐도 획이 끊기지 않는다
    cmds = r.feed(None, 2) + r.feed("index_up", 2)
    results.append(check("brief hand loss keeps stroke", actions(cmds) == ["draw", "draw"], actions(cmds)))

    # 5. 두 손가락으로 바꾸면 up 한 번 뒤 erase(좌표 포함), 다시 검지로 바꾸면 up 뒤 draw
    cmds = r.feed("two_fingers", 6)
    results.append(check("draw -> two_fingers sends up then erase (backfilled)",
                         actions(cmds) == ["draw"] * 3 + ["up"] + ["erase"] * 6, actions(cmds)))
    results.append(check("erase carries x, y", all("x" in c and "y" in c for c in cmds if c["action"] == "erase")))
    cmds = r.feed("index_up", 6)
    results.append(check("erase -> index_up sends up then draw (backfilled)",
                         actions(cmds) == ["erase"] * 3 + ["up"] + ["draw"] * 6, actions(cmds)))
    cmds = r.feed("open_palm", 6)
    results.append(check("draw -> open_palm sends up then hover",
                         actions(cmds) == ["draw"] * 3 + ["up"] + ["hover"] * 3, actions(cmds)))

    # 6. 손이 오래 사라지면 up 한 번, 이후 조용
    r = Runner()
    r.feed("index_up", 6)
    cmds = r.feed(None, 10)
    results.append(check("long hand loss sends one up", actions(cmds) == ["up"], actions(cmds)))

    # 7. 다시 나타난 손은 이전 위치에서 튀지 않는다 (보정 상태 초기화)
    cmds = r.feed("index_up", 6, dx=-0.2)
    expected_x = logic.to_canvas(0.5 - 0.2 - 0.04, 0.15, 0.85)
    results.append(check("smoothing reset after loss", abs(cmds[0]["x"] - expected_x) < 1e-3, cmds[0]))

    # 8. 주먹 1초 유지 → clear 정확히 한 번
    r = Runner()
    r.feed("index_up", 6)
    fist_start = r.frame
    cmds = r.feed("fist", 2 * FPS)
    acts = actions(cmds)
    results.append(check("fist sends up once and clear once", acts.count("clear") == 1 and acts.count("up") == 1, acts))
    clear_frame = next((c["frame_id"] for c in cmds if c["action"] == "clear"), -1)
    results.append(check("clear ~1s after fist first appears", FPS <= clear_frame - fist_start <= FPS + 2, clear_frame - fist_start))

    # 9. 짧은 주먹은 clear를 보내지 않는다
    r = Runner()
    cmds = r.feed("fist", FPS // 2) + r.feed("index_up", 6)
    results.append(check("short fist does not clear", "clear" not in actions(cmds), actions(cmds)))

    # 10. 세션끼리 상태가 섞이지 않는다
    store = logic.SessionStore(config=logic.Config())
    for i in range(6):
        store.process({"sid": "a", "frame_id": i, "ts": i * FRAME_MS, "hand": {"score": 0.9, "points": fake_hand(POSE["index_up"])}})
    out_b = store.process({"sid": "b", "frame_id": 0, "ts": 0, "hand": {"score": 0.9, "points": fake_hand(POSE["index_up"])}})
    results.append(check("sessions are independent", out_b == [], out_b))

    # 11. send_landmarks: 손이 보이는 프레임마다 landmarks 1개, 손이 사라지면 빈 목록 1번
    store = logic.SessionStore(config=logic.Config(send_landmarks=True))
    hand = {"score": 0.9, "points": fake_hand(POSE["index_up"])}
    per_frame = [store.process({"sid": "l", "frame_id": i, "ts": i * FRAME_MS, "hand": hand}) for i in range(6)]
    lms = [[c for c in cmds if c["action"] == "landmarks"] for cmds in per_frame]
    results.append(check("landmarks every frame with 21 points",
                         all(len(l) == 1 and len(l[0]["params"]["points"]) == 21 for l in lms), [len(l) for l in lms]))
    results.append(check("landmarks do not change actions",
                         [c["action"] for cmds in per_frame for c in cmds if c["action"] != "landmarks"] == ["draw"] * 6))
    lost = [c for i in range(6, 12) for c in store.process({"sid": "l", "frame_id": i, "ts": i * FRAME_MS, "hand": None})]
    cleared = [c for c in lost if c["action"] == "landmarks"]
    results.append(check("hand loss clears landmarks once", len(cleared) == 1 and cleared[0]["params"]["points"] == [], actions(lost)))

    # 12. 가장자리: 손목이 아래 테두리에 잘리면 주먹처럼 보여도 그리기를 이어 간다 (좌표는 계속)
    r = Runner()
    r.feed("index_up", 6)
    cmds = r.feed("fist", 8, dy=0.2)
    results.append(check("wrist clipped at bottom edge keeps drawing", actions(cmds) == ["draw"] * 8, actions(cmds)))
    results.append(check("drawing at edge still sends x, y", all("x" in c for c in cmds)))
    cmds = r.feed("fist", 6)
    results.append(check("same pose inside frame is recognized", "up" in actions(cmds), actions(cmds)))

    # 13. 가장자리: 검지 끝이 왼쪽 테두리에 닿으면 두 손가락으로 바뀌지 않는다
    r = Runner()
    r.feed("index_up", 6)
    cmds = r.feed("two_fingers", 8, dx=-0.46)
    results.append(check("fingertip at side edge keeps drawing", actions(cmds) == ["draw"] * 8, actions(cmds)))

    # 14. 기준 두 개: 기준선 근처(1.0)는 직전 상태를 유지한다
    r = Runner()
    r.feed("index_up", 6)
    cmds = r.feed("index_up", 8, index_ratio=1.0) + r.feed("index_up", 4, index_ratio=1.12)
    results.append(check("near-threshold finger stays extended", actions(cmds) == ["draw"] * 12, actions(cmds)))
    cmds = r.feed("index_up", 6, index_ratio=0.9)
    results.append(check("clearly folded finger becomes fist", "up" in actions(cmds), actions(cmds)))
    r = Runner()
    r.feed("fist", 6)
    cmds = r.feed("index_up", 8, index_ratio=1.0)
    results.append(check("near-threshold finger stays folded", "draw" not in actions(cmds), actions(cmds)))
    results.append(check("stateless classify unchanged at 1.0", logic.classify(fake_hand(POSE["index_up"], index_ratio=1.0)) == "fist"))

    # 15. 획 시작 채우기: 손바닥으로 움직이다 검지를 들면, 검지가 처음 보인 자리부터 선이 시작된다
    r = Runner()
    r.feed("open_palm", 6)
    hover_then = [c for i in range(4) for c in r.feed("index_up", 1, dx=0.02 * i)]
    draws = [c for c in hover_then if c["action"] == "draw"]
    results.append(check("stroke start backfilled from first index frame",
                         actions(hover_then) == ["hover"] * 3 + ["draw"] * 4 and draws[0]["x"] < draws[-1]["x"], actions(hover_then)))
    results.append(check("backfilled points follow hand path", [c["x"] for c in draws] == sorted(c["x"] for c in draws), [c["x"] for c in draws]))

    # 16. One Euro: 멈춘 손의 떨림은 EMA보다 잘 줄이고, 빠른 손에는 EMA보다 덜 늦는다
    import random
    rnd = random.Random(0)
    f, ema, a = logic.OneEuroFilter(1.0, 10.0), None, 0.4
    raw_still, oe_still, ema_still = [], [], []
    for i in range(60):
        x = 0.5 + rnd.uniform(-0.01, 0.01)
        ox, _ = f.update(x, 0.5, i / FPS)
        ema = x if ema is None else a * x + (1 - a) * ema
        if i >= 15:
            raw_still.append(x); oe_still.append(ox); ema_still.append(ema)
    spread = lambda v: max(v) - min(v)
    results.append(check("one euro reduces still-hand jitter more than EMA",
                         spread(oe_still) < spread(ema_still) < spread(raw_still), (spread(raw_still), spread(ema_still), spread(oe_still))))
    f, ema = logic.OneEuroFilter(1.0, 10.0), None
    for i in range(20):
        x = 0.2 + 0.03 * i  # 초당 화면 폭의 45%
        ox, _ = f.update(x, 0.5, i / FPS)
        ema = x if ema is None else a * x + (1 - a) * ema
    results.append(check("one euro lags less than EMA on fast moves", x - ox < x - ema, (round(x - ox, 4), round(x - ema, 4))))

    # 17. pinch(OK 사인) → next_color 한 번만
    r = Runner()
    r.feed("index_up", 6)
    cmds = r.feed("pinch", 10)
    results.append(check("pinch sends next_color once", actions(cmds).count("next_color") == 1 and actions(cmds).count("up") == 1, actions(cmds)))
    cmds = r.feed("open_palm", 6) + r.feed("pinch", 6)
    results.append(check("pinch again after release sends next_color again", actions(cmds).count("next_color") == 1, actions(cmds)))
    results.append(check("classify pinch", logic.classify(fake_hand(POSE["pinch"], pinch=True)) == "pinch"))
    fist_thumb_near = fake_hand(POSE["fist"])
    fist_thumb_near[4] = [fist_thumb_near[8][0] - 0.01, fist_thumb_near[8][1] + 0.01, 0.0]
    results.append(check("fist with thumb near index is not pinch", logic.classify(fist_thumb_near) == "fist"))
    results.append(check("three fingers without touching thumb is not pinch", logic.classify(fake_hand(POSE["pinch"])) == "unknown"))

    # 18. 형식 검사
    bad = [{}, {"sid": "x", "frame_id": 1, "ts": 1, "hand": {"points": [[0, 0]] * 5}}, {"sid": "x", "frame_id": "1", "ts": 1, "hand": None}]
    rejected = 0
    for msg in bad:
        try:
            logic.parse_landmarks(msg)
        except ValueError:
            rejected += 1
    results.append(check("malformed input rejected", rejected == len(bad), f"{rejected}/{len(bad)}"))

    print(f"\n{sum(results)}/{len(results)} passed")
    return all(results)


def replay(path):
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    messages = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    store = logic.SessionStore(config=logic.Config())
    for msg in messages:
        for cmd in store.process(msg):
            print(json.dumps(cmd, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        replay(sys.argv[1])
    else:
        sys.exit(0 if run_scenarios() else 1)
