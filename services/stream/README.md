# stream (8002) — 휴대폰 카메라 페이지, 프레임 수신·전달

- 담당: 임우진
- 컨테이너 주소: `http://stream:8002`
- 구조도(`docs/architecture.html`)의 ①③ ④ ⑧을 맡는다. 형식은 `docs/contracts/README.md`를 따른다.

## 하는 일

```
휴대폰 ──①(wss /ws/stream)──▶ security(nginx) ──③(X-Session-Id 헤더)──▶ stream ──④ POST /frames──▶ vision
                                                                          └────⑧ POST /events──▶ web
```

1. `GET /m/`: 휴대폰 카메라 페이지. QR 주소(`/m/?token=…`)로 들어오면 security가 토큰을 확인하고 여기로 넘긴다.
2. `WebSocket /ws/stream`: 휴대폰이 `{"frame_id": 42, "jpeg": "<base64>"}`를 보내면 `{"type": "ack", "frame_id": 42, "dropped": false}`로 답한다. 휴대폰은 ack를 받은 뒤 다음 프레임을 보낸다.
3. 프레임을 받은 시각을 `ts`(unix ms)로 붙여 vision `POST /frames`에 JPEG 바이트로 보낸다 (`X-Session-Id`, `X-Frame-Id`, `X-Ts` 헤더). vision이 `200`으로 답하면 ack를 보낸다.
4. 휴대폰이 연결되거나 끊기면 web `POST /events`로 `connected`·`disconnected`를 알린다.

### 처리 규칙

- **최신 프레임만**: vision 응답을 기다리는 중에 들어온 프레임은 큐에 쌓지 않고 바로 버린다 (`dropped: true, reason: "busy"`).
- **시간 제한 200ms**: vision 요청 전체가 200ms를 넘으면 그 프레임은 버린다 (`reason: "vision_timeout"`).
- **세션**: security(nginx)가 붙인 `X-Session-Id` 헤더만 믿는다. 없으면 `4401`로 연결을 닫는다. 같은 세션에 휴대폰이 다시 붙으면 이전 연결을 `4409`로 닫는다.

### 휴대폰 페이지와 주고받는 메시지

`docs/contracts/README.md`의 ①③ 형식에 아래 두 가지를 더했다. 둘 다 stream이 만든 카메라 페이지와 stream 사이에서만 쓴다.

| 방향 | 메시지 | 뜻 |
|---|---|---|
| stream → 휴대폰 | `{"type": "config", "width": 320, "height": 240, "quality": 0.6, "max_fps": 15}` | 연결 직후 1번. 촬영 크기·품질·fps를 서버 설정(환경 변수)으로 바꿀 수 있게 한다 |
| stream → 휴대폰 | ack의 `reason` | 버린 이유: `busy` `vision_timeout` `vision_unreachable` `vision_<상태 코드>` `vision_disabled` `invalid_frame` |

카메라 페이지는 기본으로 전면 카메라를 쓰고(미리보기만 거울처럼 보임), **카메라 전환** 버튼으로 후면으로 바꿀 수 있다. 보내는 프레임은 뒤집지 않는다 (좌우 반전은 vision에서 한 번만).

## 실행

```bash
docker compose up --build stream
```

### security 없이 PC에서 확인

휴대폰 카메라는 HTTPS에서만 열리므로, security가 준비되기 전에는 PC 브라우저(`localhost`는 HTTPS 없이도 카메라가 열린다)로 확인한다. 루트 `.env`에 개발용 세션을 넣는다.

```
STREAM_DEV_SID=dev
```

`http://localhost:8002/m/?sid=dev`를 열고 **촬영 시작**을 누른다. vision이 아직 뼈대라 프레임은 `vision_501`로 버려지는 게 정상이다. 처리 현황은 `http://localhost:8002/sessions`에서 본다. `STREAM_DEV_SID`는 개발할 때만 쓴다 (켜 두면 헤더 없이도 세션을 받는다).

### vision 없이 프레임 흐름 확인 (`dev/fake_vision.py`)

vision이 준비되기 전에는 PC에서 vision 대역을 띄워 stream → vision 흐름을 확인한다. 표준 라이브러리만 쓴다.

```bash
# 루트 .env
STREAM_DEV_SID=dev
STREAM_VISION_URL=http://host.docker.internal:8003/frames

docker compose up --build stream web               # vision 컨테이너는 띄우지 않는다 (8003 포트가 겹침)
python services/stream/dev/fake_vision.py          # 받은 프레임을 1초마다 출력, 30ms 뒤 {"ok": true}
```

- `--delay 0.3`: 느린 vision 흉내. 200ms 시간 제한에 걸려 프레임이 버려지는 것을 확인한다.
- `--landmarks http://localhost:8004/landmarks`: gesture에 ⑤ `hand: null`도 보낸다 (gesture 담당 테스트용).

### web과 함께 확인 (⑧ 연결 알림)

같은 세션 값으로 PC 그림판과 카메라 페이지를 연다.

1. `http://localhost:8000/?sid=test` (그림판, 세션 발급·QR을 건너뜀)
2. `http://localhost:8002/m/?sid=test` → **촬영 시작**

그림판 위쪽이 "휴대폰 연결됨"으로 바뀌고 QR 창이 닫힌다. 카메라 페이지를 닫으면 "휴대폰 끊김"으로 바뀐다.

### 테스트

```bash
docker compose run --rm --no-deps stream pytest -q
```

## 환경 변수 (루트 `.env`, 모두 선택)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `STREAM_VISION_URL` | `http://vision:8003/frames` | ④ 프레임을 보낼 곳. 비우면 보내지 않는다 (`vision_disabled`) |
| `STREAM_EVENTS_URL` | `http://web:8000/events` | ⑧ 연결·끊김을 알릴 곳. 비우면 알리지 않는다 |
| `STREAM_TIMEOUT_MS` | `200` | vision 응답을 기다리는 최대 시간 |
| `STREAM_WIDTH`, `STREAM_HEIGHT` | `320`, `240` | 휴대폰이 보내는 프레임 크기 (세로로 찍으면 240×320). 비율은 유지한다 |
| `STREAM_JPEG_QUALITY` | `0.6` | JPEG 품질 |
| `STREAM_MAX_FPS` | `15` | 최대 초당 프레임 수 |
| `STREAM_DEV_SID` | (없음) | 개발용 세션. 정하면 `X-Session-Id`가 없을 때 `?sid=` 또는 이 값을 쓴다 |

## 주소

| 주소 | 누가 | 하는 일 |
|---|---|---|
| `GET /health` | compose | 상태 |
| `GET /m/` | 휴대폰 (security 경유) | 카메라 페이지 |
| `WS /ws/stream` | 휴대폰 (security 경유) | 프레임 보내기, ack 받기 |
| `GET /sessions` | 개발자 | 세션별 받은·보낸·버린 프레임 수, 마지막 vision 응답 시간 |
