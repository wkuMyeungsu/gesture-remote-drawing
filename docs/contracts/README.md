# 서비스 간 주고받는 데이터

연결 번호(①~⑧)와 전체 구조는 `docs/architecture.html`을 따른다.

상태: **제안**(문서만 있음) → **구현**(양쪽이 이 형식대로 구현해 dev에 병합) → **합의**(양쪽 담당자가 이 문서를 확인). 형식을 바꿀 때는 이 문서부터 고치고 양쪽 담당자에게 PR 리뷰를 받는다.

| 번호 | 보내는 쪽 → 받는 쪽 | 방식 | 상태 |
|---|---|---|---|
| ② | PC 브라우저 → security | `POST /api/session` | 구현 |
| ① ③ | 휴대폰 ↔ stream (security 경유) | WebSocket `/ws/stream` | 구현 |
| ④ | stream → vision | `POST http://vision:8003/frames` | 구현 |
| ⑤ | vision → gesture | `POST http://gesture:8004/landmarks` | 구현 |
| ⑥ | gesture → web | `POST http://web:8000/actions` | 구현 |
| ⑦ ② | web → PC 브라우저 (security 경유) | WebSocket `/ws/board?sid=…` | 구현 |
| ⑧ | stream → web | `POST http://web:8000/events` | 구현 |

## 공통

| 필드 | 형식 | 뜻 |
|---|---|---|
| `sid` | 문자열 (UUID) | 세션 ID. PC 한 대와 휴대폰 한 대가 한 세션 |
| `frame_id` | 정수 | 휴대폰이 0부터 1씩 올려 붙이는 프레임 번호 |
| `ts` | 정수 (유닉스 ms) | stream이 프레임을 받은 시각. 이후 모든 메시지에 그대로 전달 |
| `x`, `y` | 실수 0~1 | 정규화 좌표. 왼쪽 위가 (0, 0), 좌우 반전이 끝난 화면 기준. ⑤는 프레임 기준, ⑥은 그림판 영역을 캔버스 전체로 늘린 캔버스 기준 |

내부 요청(④⑤⑥⑧)은 성공하면 `200`과 `{"ok": true}`, 형식이 틀리면 `400`을 돌려준다. 응답에 다른 필드가 더 붙을 수 있다 (예: web의 `delivered`). 각 요청의 타임아웃은 200ms이다. ④⑤는 프레임 하나를 처리하는 동안 차례로 기다리고, ⑥은 gesture가 큐에 넣어 따로 보내므로 프레임 처리를 막지 않는다.

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

- `pair_url`의 호스트는 `.env`의 `SECURITY_CERT_HOST`(PC 내부 IP)다. 비어 있으면 요청의 `Host`를 쓴다.
- 발급이 너무 잦으면 `429`, 세션이 가득 차면 `503`.
- `token`은 1회용이다. 휴대폰이 `/m/?token=…`로 접속하면 security가 `sid` 쿠키(`HttpOnly; Secure; SameSite=Strict`)를 발급하고 토큰을 폐기한다. 만료되거나 이미 쓴 토큰이면 `401` 안내 페이지를 보여 준다.
- 이후 휴대폰 요청은 쿠키로 확인하고, nginx가 `X-Session-Id: <sid>` 헤더를 붙여 stream으로 넘긴다.

## ①③ 프레임 전송 — 휴대폰 ↔ stream

`wss://<PC IP>/ws/stream` (쿠키로 인증)

휴대폰 → stream: 텍스트 메시지 1개 = 프레임 1장

```json
{ "frame_id": 42, "jpeg": "<base64>" }
```

- 해상도 320×240, JPEG 품질 0.6, 최대 15fps. 연결 직후 stream이 `{"type": "config", …}`로 이 값과 그림판 영역(`draw_zone`)을 알려 준다.
- 휴대폰 방향과 상관없이 항상 320×240(가로 4:3)으로 보낸다. 카메라 비율이 다르면 가운데를 잘라 낸다.
- 프레임은 뒤집지 않고 보낸다 (좌우 반전은 vision에서 한 번만).
- 다음 프레임은 아래 `ack`를 받은 뒤에 보낸다.

stream → 휴대폰

```json
{ "type": "ack", "frame_id": 42, "dropped": false }
{ "type": "ack", "frame_id": 43, "dropped": true, "reason": "vision_timeout" }
```

`dropped: true`는 그 프레임을 버렸다는 뜻이고, `reason`에 이유가 붙는다 (`busy` `vision_timeout` `vision_unreachable` `vision_<상태 코드>` `vision_disabled` `invalid_frame`). 전체 목록과 `config` 형식은 `services/stream/README.md`.

## ④ 프레임 분석 요청 — stream → vision

`POST http://vision:8003/frames`

- 본문: JPEG 바이트 (`Content-Type: image/jpeg`)
- 헤더: `X-Session-Id`, `X-Frame-Id`, `X-Ts`

vision은 응답을 먼저 돌려주지 않고, ⑤를 보낸 뒤 `{"ok": true}`로 답한다. stream은 이 응답을 받아야 다음 프레임을 보낸다. 헤더·JPEG가 틀리면 `400`, ⑤가 실패하거나 200ms를 넘기면 `502`로 답한다.

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
- `x`, `y`는 0~1로 자른다 (화면 밖 점은 0 또는 1). `z`는 손목 기준 상대 깊이 (작을수록 카메라에 가까움).
- `handedness`는 사용자 기준 실제 손 (`"Left"` / `"Right"`).
- 손이 없으면 `"hand": null`.
- 손은 하나만 보낸다. 손이 여럿이면 앞 프레임에서 보낸 손에 가장 가까운 손을, 처음에는 `score`가 가장 높은 손을 고른다 (vision이 세션마다 손을 추적한다).

gesture는 받은 메시지를 판별해 ⑥ 명령을 큐에 넣고 바로 `{"ok": true}`로 답한다 (⑥을 기다리지 않는다). 형식이 틀리면 `400`.

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

- web은 `{"ok": true, "delivered": <받은 PC 브라우저 수>}`로 답한다. 그 `sid`를 보고 있는 화면이 없으면 `delivered: 0`이고, 그래도 `200`이다.
- `sid`·`action`이 없거나, `draw`·`erase`·`hover`인데 `x`·`y`가 없으면 `400`.
- ⑤ 한 번에 ⑥이 여러 번 갈 수 있다. 획 기능이 바뀌면 `up`을 먼저 보내고, 새 획이 시작되면 동작을 확인하느라 기다린 프레임의 좌표부터 보내고, 프레임마다 `landmarks`를 덧붙인다.
- gesture는 큐로 보낸 순서를 지킨다. web이 막혀 큐(256개)가 차면 새 명령을 버린다.

### `action` 표

새 기능은 이 표에 한 줄을 추가하는 것으로 시작한다 (gesture·web 합의). 현재 도구·색·굵기 같은 상태는 web이 가진다. gesture는 "다음 색"처럼 바꾸려는 의도만 보내고 값 자체는 보내지 않는다.

| `action` | 종류 | 뜻 | 필드 |
|---|---|---|---|
| `draw` | 연속 | 펜을 내린 채 이동 (선을 그림) | `x`, `y` |
| `erase` | 연속 | 지우개를 댄 채 이동 (지나간 자리를 지움) | `x`, `y` |
| `hover` | 연속 | 펜을 든 채 이동 (커서만) | `x`, `y` |
| `up` | 1회 | 펜 들기 (손이 사라졌을 때, 획 기능이 바뀔 때 포함) | 없음 |
| `clear` | 1회 | 캔버스 전체 지우기 (실행 취소로 되살릴 수 있음) | 없음 |
| `next_color` | 1회 | 펜 색을 팔레트의 다음 색으로 | 없음 |
| `landmarks` | 연속 (표시 전용) | 손 뼈대를 그림판 위에 겹쳐 표시. 그림에는 영향 없음. 손이 사라지면 `points: []` 1번. `gesture`에는 지금 동작 이름이 들어가고, web은 `fist`일 때 전체 지우기 카운트다운을 띄운다 | `params.points` (`[[x, y], …]` 21개, 프레임 기준), `params.zone` (`[lo, hi]`, 캔버스로 늘리는 범위) |

- 연속 기능은 프레임마다 보내고, 1회 기능은 한 번 실행할 때 한 번만 보낸다. 한 번만 보내도록 판단하는 방법은 gesture가 정한다.
- `landmarks`는 gesture의 `GESTURE_SEND_LANDMARKS`(기본 `1`)로 끌 수 있다.
- web은 표에 없는 `action`을 받으면 무시한다.

### 대응표 `services/gesture/mapping.json`

동작 이름과 기능 이름의 짝은 gesture 담당이 이 파일로 관리한다. 짝을 바꾸는 데에는 다른 파트와 합의가 필요 없다. 이 문서에 복사해 두면 어긋나므로, 현재 짝은 파일을 직접 본다.

`{"동작 이름": "action", …}` 형식이다. 파일에 없는 기능(`action` 표에는 있지만 짝이 없는 것)은 손으로 실행할 수 없다. `landmarks`는 대응표와 상관없이 프레임마다 보낸다.

## ⑦② 화면 반영 — web → PC 브라우저

`ws://localhost/ws/board?sid=…` (PC에서 `https://<PC IP>/`로 열었으면 `wss://<PC IP>/ws/board?sid=…`)

web은 ⑥에서 받은 JSON에서 `sid`와 빈 필드를 빼고 `"type": "action"`을 붙여 같은 `sid`의 PC 브라우저에 보낸다. ⑧ 이벤트도 같은 통로로 보낸다. PC 브라우저 → web 메시지는 없다.

브라우저는 손·마우스 획을 같은 목록에 쌓는다. 마우스 도구(실행 취소·다시 실행·저장 포함)는 브라우저 안에서만 동작하고 서버로 보내지 않는다. gesture 메시지가 오면 0.8초 동안 마우스 입력을 받지 않는다 (손 우선).

```json
{ "type": "action", "gesture": "index_up", "confidence": 0.95, "action": "draw", "x": 0.531, "y": 0.402, "frame_id": 42, "ts": 1793253600123 }
{ "type": "event", "event": "connected" }
```

## ⑧ 연결 알림 — stream → web

`POST http://web:8000/events`

```json
{ "sid": "5f0c2a9e-…", "event": "connected" }
```

`event`: `connected`(휴대폰 WebSocket 연결), `disconnected`(끊김. stream이 꺼질 때도 연결돼 있던 세션마다 보낸다). 그 밖의 값은 web이 `400`으로 답한다.
