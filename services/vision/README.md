# vision (8003) — 전처리, 손 좌표 21개 추출

- 담당: 이진아
- 컨테이너 주소: `http://vision:8003`
- 구조도(`docs/architecture.html`)의 ④ 받기와 ⑤ 보내기를 맡는다. 형식은 `docs/contracts/README.md`를 따른다.

## 하는 일

```
stream ──④ POST /frames (JPEG, X-Session-Id·X-Frame-Id·X-Ts)──▶ vision ──⑤ POST /landmarks──▶ gesture
```

1. `POST /frames`로 JPEG 한 장과 헤더 3개(`X-Session-Id`, `X-Frame-Id`, `X-Ts`)를 받는다.
2. 전처리: JPEG 디코딩 → 긴 변이 `VISION_MAX_SIDE`보다 크면 축소 → **좌우 반전** → BGR→RGB.
3. MediaPipe Hands로 손을 찾아 손 하나의 좌표 21개를 고른다. 세션(휴대폰)마다 앞 프레임의 손을 이어서 추적하고, 손이 여럿이면 앞 프레임 손에 가장 가까운 손을 고른다 (처음에는 `score`가 가장 높은 손).
4. gesture `POST /landmarks`로 보낸 뒤 stream에 `{"ok": true}`로 답한다 (stream은 이 응답을 받아야 다음 프레임을 보낸다).

보내는 형식 (⑤):

```json
{ "sid": "5f0c2a9e-…", "frame_id": 42, "ts": 1793253600123,
  "hand": { "handedness": "Right", "score": 0.94, "points": [[0.512, 0.640, 0.0], "… 21개"] } }
```

- `points`: MediaPipe Hands 순서의 `[x, y, z]`. `x`, `y`는 0~1 (좌우 반전이 끝난 화면 기준, 화면 밖으로 나간 점은 0 또는 1로 자른다). `z`는 손목 기준 상대 깊이(작을수록 카메라에 가까움).
- 손이 없으면 `"hand": null`.
- `handedness`: 실제 손(사용자 기준 왼손·오른손). MediaPipe는 반전된(셀카) 이미지를 가정하므로, `VISION_MIRROR=0`이면 라벨을 바꿔서 보낸다.

### 처리 규칙

- **좌우 반전은 vision에서 한 번만**: 휴대폰은 뒤집지 않은 프레임을 보낸다. gesture는 `GESTURE_MIRROR=0`(기본)으로 두면 된다.
- **세션마다 추적**: 세션마다 MediaPipe Hands를 하나씩 두고 추적 모드(`static_image_mode=False`)로 돌린다. 프레임마다 처음부터 찾으면(정지 이미지 모드) 점이 프레임마다 흔들리고, 두 손·오검출 사이를 오가며 좌표가 튄다. 추적이 끊기면 그 프레임에서 다시 찾는다. `VISION_SESSION_IDLE_S` 동안 프레임이 없는 세션은 정리하고, 최대 `VISION_MAX_SESSIONS`개까지 둔다. vision을 여러 개로 늘리려면 같은 세션은 같은 vision으로 보내야 한다. 남은 작은 떨림 보정은 gesture가 한다.
- **한 번에 한 프레임**: MediaPipe 그래프는 동시에 부를 수 없어 세션별 잠금으로 순서대로 처리한다. 320×240 한 장에 약 20~30ms (lite 모델, CPU).
- **응답 코드**: 성공 `200 {"ok": true}`. 헤더·JPEG가 틀리면 `400`. gesture가 실패·시간 초과(200ms)면 `502` → stream 쪽에는 `vision_502`로 보인다.

## 실행

```bash
docker compose up --build vision
```

### 사진 한 장으로 확인 (stream·gesture 없이)

`POST /detect`는 gesture로 보내지 않고 손 좌표를 바로 돌려준다. 사진 한 장이므로 추적 없이 찾는다.

```bash
curl -X POST http://localhost:8003/detect -H "Content-Type: image/jpeg" --data-binary @손사진.jpg
python services/vision/dev/try_image.py 손사진.jpg --draw out.png   # 점을 그린 그림 저장 (opencv-python 필요)
```

### stream과 같이

```bash
docker compose up --build stream vision gesture web
```

`http://localhost:8002/sessions`(stream)와 `http://localhost:8003/stats`(vision)에서 프레임 수, 손 찾은 수, 처리 시간을 본다.

### 테스트

```bash
docker compose run --rm --no-deps vision pytest -q
```

## 환경 변수 (루트 `.env`, 모두 선택)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `VISION_GESTURE_URL` | `http://gesture:8004/landmarks` | ⑤ 손 좌표를 보낼 곳. 비우면 보내지 않고 `200`으로 답한다 |
| `VISION_TIMEOUT_MS` | `200` | gesture 응답을 기다리는 최대 시간 |
| `VISION_MIRROR` | `1` | 좌우 반전. `0`이면 반전하지 않는다 |
| `VISION_MAX_SIDE` | `640` | 긴 변이 이보다 큰 프레임은 줄여서 처리 |
| `VISION_MAX_FRAME_BYTES` | `1048576` | 받는 JPEG 최대 크기 |
| `VISION_MODEL_COMPLEXITY` | `0` | `0` lite(빠름), `1` full(조금 더 정확, 약 1.3배 느림) |
| `VISION_MIN_DETECTION` | `0.5` | 손으로 인정할 최소 신뢰도 |
| `VISION_MAX_HANDS` | `1` | 찾을 손 개수 (보내는 건 하나). 2 이상이면 손이 모자랄 때마다 손바닥 검출을 다시 돌려 느려지고 오검출이 늘어난다 |
| `VISION_TRACKING` | `1` | 세션마다 앞 프레임의 손을 이어서 추적. `0`이면 프레임마다 처음부터 찾는다 (예전 방식) |
| `VISION_MIN_TRACKING` | `0.5` | 추적을 이어 갈 최소 신뢰도. 낮추면 덜 끊기고, 높이면 빠른 손에서 더 자주 다시 찾는다 |
| `VISION_MAX_SESSIONS` | `4` | 동시에 추적할 세션 수 (세션마다 MediaPipe 그래프 하나) |
| `VISION_SESSION_IDLE_S` | `30` | 이 시간(초) 동안 프레임이 없는 세션의 추적기는 정리 |

## 주소

| 주소 | 누가 | 하는 일 |
|---|---|---|
| `GET /health` | compose | 상태 |
| `POST /frames` | stream | ④ 프레임 받기 → ⑤ gesture로 전달 |
| `POST /detect` | 개발자 | JPEG 한 장의 손 좌표를 바로 돌려줌 |
| `GET /stats` | 개발자 | 받은 수, 손 찾은 수, 손 없음, 400 수, gesture 전송 성공·실패, 처리 시간, 추적 중인 세션 수 |

## 의존성 메모

MediaPipe는 `0.10.18`을 쓴다. 모델 파일이 패키지 안에 들어 있어 따로 내려받지 않고, linux x86_64와 arm64(Apple Silicon Mac의 Docker) 휠이 모두 있는 마지막 버전이다. 기본 의존성에 딸려 오는 jax·jaxlib(약 350MB)는 쓰지 않으므로, `requirements.txt`에 필요한 것만 고정하고 mediapipe는 `--no-deps`로 설치한다 (`requirements-mediapipe.txt`).
