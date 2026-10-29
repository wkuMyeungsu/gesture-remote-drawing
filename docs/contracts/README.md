# 서비스 간 주고받는 데이터

연결 번호(①~⑧)와 전체 구조는 `docs/architecture.html`을 따른다. 아래는 모두 제안 단계이고, 양쪽 담당자가 확인하면 표의 상태를 "합의"로 바꾼다.

| 번호 | 보내는 쪽 → 받는 쪽 | 방식 | 상태 |
|---|---|---|---|
| ② | PC 브라우저 → security | `POST /api/session` | 제안 |
| ① ③ | 휴대폰 ↔ stream (security 경유) | WebSocket `/ws/stream` | 제안 |
| ④ | stream → vision | `POST http://vision:8003/frames` | 제안 |
| ⑤ | vision → gesture | `POST http://gesture:8004/landmarks` | 제안 |
| ⑥ | gesture → web | `POST http://web:8000/actions` | 제안 |
| ⑦ ② | web → PC 브라우저 (security 경유) | WebSocket `/ws/board?sid=…` | 제안 |
| ⑧ | stream → web | `POST http://web:8000/events` | 제안 |

## 공통

| 필드 | 형식 | 뜻 |
|---|---|---|
| `sid` | 문자열 (UUID) | 세션 ID. PC 한 대와 휴대폰 한 대가 한 세션 |
| `frame_id` | 정수 | 휴대폰이 0부터 1씩 올려 붙이는 프레임 번호 |
| `ts` | 정수 (유닉스 ms) | stream이 프레임을 받은 시각. 이후 모든 메시지에 그대로 전달 |
| `x`, `y` | 실수 0~1 | 정규화 좌표. 왼쪽 위가 (0, 0), 좌우 반전이 끝난 화면 기준 |

내부 요청(④⑤⑥⑧)은 성공하면 `200`과 `{"ok": true}`, 형식이 틀리면 `400`을 돌려준다. 각 요청의 타임아웃은 200ms이다.

## ② 세션 발급 — PC 브라우저 → security

`POST /api/session` (본문 없음)

```json
{
  "sid": "5f0c2a9e-0d7b-4c1e-9a3f-2b8e6d4c1a70",
  "token": "q8Zr3kLm",
  "pair_url": "https://192.168.0.12/m/?token=q8Zr3kLm",
  "expires_in": 60
}
```

- `token`은 1회용이다. 휴대폰이 `/m/?token=…`로 접속하면 security가 `sid` 쿠키(`HttpOnly; Secure; SameSite=Strict`)를 발급하고 토큰을 폐기한다.
- 이후 휴대폰 요청은 쿠키로 확인하고, nginx가 `X-Session-Id: <sid>` 헤더를 붙여 stream으로 넘긴다.

## ①③ 프레임 전송 — 휴대폰 ↔ stream

`wss://<PC IP>/ws/stream` (쿠키로 인증)

휴대폰 → stream: 텍스트 메시지 1개 = 프레임 1장

```json
{ "frame_id": 42, "jpeg": "<base64>" }
```

- 해상도 320×240, JPEG 품질 0.6, 최대 15fps.
- 다음 프레임은 아래 `ack`를 받은 뒤에 보낸다.

stream → 휴대폰

```json
{ "type": "ack", "frame_id": 42, "dropped": false }
```

`dropped: true`는 vision이 바빠서 그 프레임을 버렸다는 뜻이다.

## ④ 프레임 분석 요청 — stream → vision

`POST http://vision:8003/frames`

- 본문: JPEG 바이트 (`Content-Type: image/jpeg`)
- 헤더: `X-Session-Id`, `X-Frame-Id`, `X-Ts`

vision은 응답을 먼저 돌려주지 않고, ⑤를 보낸 뒤 `{"ok": true}`로 답한다. stream은 이 응답을 받아야 다음 프레임을 보낸다.

## ⑤ 손 좌표 — vision → gesture

`POST http://gesture:8004/landmarks`

```json
{
  "sid": "5f0c2a9e-…",
  "frame_id": 42,
  "ts": 1793253600123,
  "hand": {
    "handedness": "Right",
    "score": 0.94,
    "points": [[0.512, 0.640, 0.0], [0.540, 0.601, -0.012], "… 21개"]
  }
}
```

- `points`는 MediaPipe Hands 순서의 21개 점 `[x, y, z]`. 0 손목, 4 엄지 끝, 8 검지 끝, 12 중지 끝, 16 약지 끝, 20 새끼 끝.
- 손이 없으면 `"hand": null`.
- 손이 둘 이상이면 `score`가 가장 높은 손 하나만 보낸다.

## ⑥ 기능 실행 요청 — gesture → web

`POST http://web:8000/actions`

gesture는 "어떤 동작을 알아봤는지(`gesture`)"와 "어떤 기능을 실행할지(`action`)"를 함께 보낸다. web은 `action`으로만 동작하고, `gesture`·`confidence`는 화면 표시(디버깅·시연)에만 쓴다.

```json
{ "sid": "5f0c2a9e-…", "frame_id": 42, "ts": 1793253600123, "gesture": "index_up", "confidence": 0.95, "action": "draw", "x": 0.531, "y": 0.402 }
{ "sid": "5f0c2a9e-…", "frame_id": 57, "ts": 1793253601120, "gesture": "fist_hold", "confidence": 0.90, "action": "clear" }
```

| 필드 | 형식 | 정하는 쪽 | 뜻 |
|---|---|---|---|
| `gesture` | 문자열 | gesture 담당이 자유롭게 | 인식한 동작 이름. 손이 없으면 `"none"` |
| `confidence` | 실수 0~1 | gesture | 판별 신뢰도 |
| `action` | 문자열 | 아래 표 (합의) | 실행할 기능 |
| `x`, `y` | 실수 0~1 | gesture | 좌표가 필요한 기능에만 |
| `params` | 객체 (선택) | 아래 표 (합의) | 기능별 추가 값. 형식을 바꾸지 않고 값을 늘릴 때 쓴다 |

### `action` 표

새 기능은 이 표에 한 줄을 추가하는 것으로 시작한다 (gesture·web 합의). 현재 도구·색·굵기 같은 상태는 web이 가진다. gesture는 "다음 색"처럼 바꾸려는 의도만 보내고 값 자체는 보내지 않는다.

| `action` | 종류 | 뜻 | 필드 |
|---|---|---|---|
| `draw` | 연속 | 펜을 내린 채 이동 (선을 그림) | `x`, `y` |
| `erase` | 연속 | 지우개를 댄 채 이동 (지나간 자리를 지움) | `x`, `y` |
| `hover` | 연속 | 펜을 든 채 이동 (커서만) | `x`, `y` |
| `up` | 1회 | 펜 들기 (손이 사라졌을 때 포함) | 없음 |
| `clear` | 1회 | 캔버스 전체 지우기 | 없음 |
| `undo` | 1회 | 마지막 획 취소 | 없음 |
| `next_color` | 1회 | 펜 색을 팔레트의 다음 색으로 | 없음 |
| `size_up` / `size_down` | 1회 | 펜 굵기 한 단계 키우기 / 줄이기 | 없음 |
| `save` | 1회 | 캔버스를 PNG로 저장 | 없음 |
| `landmarks` | 연속 (표시 전용) | 손 좌표 21개를 그림판 구석에 표시. 그림에는 영향 없음. 손이 사라지면 `points: []` 1번 | `params.points` (`[[x, y], …]` 21개), `params.zone` (`[lo, hi]`, 캔버스로 늘리는 범위) |

- 연속 기능은 프레임마다 보내고, 1회 기능은 한 번 실행할 때 한 번만 보낸다. 한 번만 보내도록 판단하는 방법은 gesture가 정한다.
- web은 표에 없는 `action`을 받으면 무시한다.

### 대응표 `services/gesture/mapping.json`

동작 이름과 기능 이름의 짝은 gesture 담당이 이 파일로 관리한다. 짝을 바꾸는 데에는 다른 파트와 합의가 필요 없다.

```json
{
  "index_up": "draw",
  "open_palm": "hover",
  "none": "up",
  "fist_hold": "clear",
  "swipe_left": "undo",
  "pinch": "next_color"
}
```

## ⑦② 화면 반영 — web → PC 브라우저

`wss://<PC IP>/ws/board?sid=…`

web은 ⑥에서 받은 JSON에 `"type": "action"`을 붙여 같은 `sid`의 PC 브라우저에 보낸다. ⑧ 이벤트도 같은 통로로 보낸다.

```json
{ "type": "action", "gesture": "index_up", "confidence": 0.95, "action": "draw", "x": 0.531, "y": 0.402, "frame_id": 42, "ts": 1793253600123 }
{ "type": "event", "event": "connected" }
```

## ⑧ 연결 알림 — stream → web

`POST http://web:8000/events`

```json
{ "sid": "5f0c2a9e-…", "event": "connected" }
```

`event`: `connected`(휴대폰 WebSocket 연결), `disconnected`(끊김).
