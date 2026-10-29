# 서비스 간 주고받는 데이터

연결 번호(①~⑧)와 전체 구조는 `docs/architecture.html`을 따른다. 아래는 모두 제안 단계이고, 양쪽 담당자가 확인하면 표의 상태를 "합의"로 바꾼다.

| 번호 | 보내는 쪽 → 받는 쪽 | 방식 | 상태 |
|---|---|---|---|
| ② | PC 브라우저 → security | `POST /api/session` | 제안 |
| ① ③ | 휴대폰 ↔ stream (security 경유) | WebSocket `/ws/stream` | 제안 |
| ④ | stream → vision | `POST http://vision:8003/frames` | 제안 |
| ⑤ | vision → gesture | `POST http://gesture:8004/landmarks` | 제안 |
| ⑥ | gesture → web | `POST http://web:8000/commands` | 제안 |
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

## ⑥ 그리기 명령 — gesture → web

`POST http://web:8000/commands`

```json
{ "sid": "5f0c2a9e-…", "frame_id": 42, "ts": 1793253600123, "cmd": "draw", "x": 0.531, "y": 0.402 }
```

| `cmd` | 뜻 | 추가 필드 |
|---|---|---|
| `draw` | 펜을 내린 채 이동 (선을 그림) | `x`, `y` |
| `hover` | 펜을 든 채 이동 (커서만) | `x`, `y` |
| `up` | 펜 들기 (손이 사라졌을 때 포함) | 없음 |
| `clear` | 캔버스 지우기 | 없음 |
| `color` | 펜 색 변경 | `color` (`"#e53935"` 형식) |

- 동작 판별 규칙(어떤 손 모양이 어떤 명령인지)은 gesture 담당이 `services/gesture/README.md`에 적는다.
- `clear`, `color`는 같은 손 모양이 일정 시간 유지될 때 한 번만 보낸다.

## ⑦② 화면 반영 — web → PC 브라우저

`wss://<PC IP>/ws/board?sid=…`

web은 ⑥에서 받은 JSON을 같은 `sid`의 PC 브라우저에 그대로 보낸다. ⑧ 이벤트도 같은 통로로 보낸다.

```json
{ "type": "command", "cmd": "draw", "x": 0.531, "y": 0.402, "frame_id": 42, "ts": 1793253600123 }
{ "type": "event", "event": "connected" }
```

## ⑧ 연결 알림 — stream → web

`POST http://web:8000/events`

```json
{ "sid": "5f0c2a9e-…", "event": "connected" }
```

`event`: `connected`(휴대폰 WebSocket 연결), `disconnected`(끊김).
