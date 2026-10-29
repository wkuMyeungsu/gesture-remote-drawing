# web

PC 그림판 페이지, QR 표시, gesture가 보낸 기능(`action`)을 PC 브라우저로 중계·실행한다. 메시지 형식은 `docs/contracts/README.md`.

## 주소

| 경로 | 쓰는 쪽 | 내용 |
|---|---|---|
| `GET /` | PC 브라우저 | 그림판 페이지 |
| `GET /qr.svg?data=…` | PC 브라우저 | 페어링 QR (SVG) |
| `WS /ws/board?sid=…` | PC 브라우저 | ⑦ `{"type": "action", …}`, `{"type": "event", …}` 수신 |
| `POST /actions` | gesture | ⑥ 기능 실행 요청 |
| `POST /events` | stream | ⑧ 휴대폰 연결·끊김 |
| `GET /health` | compose | 상태 확인 |

- `/actions`, `/events`는 `{"ok": true, "delivered": <받은 PC 브라우저 수>}`로 답한다. 형식이 틀리면 `400`.
- 표에 없는 `action`도 그대로 중계하고, 브라우저가 무시한다.

## 혼자 테스트하기

PC 그림판은 보통 security를 거친 `http://localhost`로 연다. web만 띄웠을 때는 `http://localhost:8000`으로 열고, 이때는 세션 API가 없어 페이지가 개발용 `sid`를 만들어 화면에 보여 준다 ("세션 API에 연결하지 못해…" 문구는 정상).

```bash
docker compose up --build web
# 브라우저로 http://localhost:8000 (또는 전체를 띄웠으면 http://localhost) 열기 → 화면의 세션 값 복사
python services/web/dev/fake_gesture.py --sid <세션>
```

원 두 개가 그려지고(두 번째는 다른 색·굵게), 1초 뒤 두 번째 원이 취소되면 정상이다.

다른 파트와 같은 `sid`로 맞추고 싶으면 `http://localhost:8000/?sid=test`처럼 주소에 붙인다 (세션 발급과 QR을 건너뛴다).

```bash
curl -X POST localhost:8000/actions -H "Content-Type: application/json" \
  -d '{"sid":"test","action":"draw","x":0.5,"y":0.5,"gesture":"index_up","confidence":0.9}'
```

## 손 표시와 거리 안내

gesture가 보내는 `landmarks`(손 좌표 21개)를 그림판 위에 그린다. 점마다 One Euro 필터로 떨림을 줄인다.

- 표시줄 **손 크기**: 손바닥 길이(손목 → 중지 뿌리) ÷ 카메라 세로. 손가락을 접어도 그대로라 주먹일 때도 같은 기준이다.
- 안내 문구 (같은 상태가 5프레임 이어지면 바뀜):
  - 손 크기 0.25 초과 → "조금 뒤로 가 주세요"
  - 판별에 쓰는 점(손목, 손가락 마디·끝)이 테두리 2% 안 → "손을 화면 안쪽으로 옮겨 주세요" (손 뼈대를 흐리게). gesture가 그 프레임을 판별하지 않는 기준(`at_edge`)과 같다
  - 손 크기 0.08 미만 → "조금 가까이 와 주세요"
- 기준값은 추정치다. 휴대폰과의 거리를 바꿔 가며 네 모서리까지 그려 보고, 흐트러지기 시작하는 손 크기로 `board.js`의 `HAND_SIZE`를 고친다.
