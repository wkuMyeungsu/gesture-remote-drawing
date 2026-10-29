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

security 없이 web만 띄우면 페이지가 `/api/session` 대신 개발용 `sid`를 만들어 화면에 보여 준다.

```bash
docker compose up --build web
# 브라우저로 http://localhost:8000 열기 → 화면의 세션 값 복사
python services/web/dev/fake_gesture.py --sid <세션>
```

다른 파트와 같은 `sid`로 맞추고 싶으면 `http://localhost:8000/?sid=test`처럼 주소에 붙인다 (세션 발급과 QR을 건너뛴다).

```bash
curl -X POST localhost:8000/actions -H "Content-Type: application/json" \
  -d '{"sid":"test","action":"draw","x":0.5,"y":0.5,"gesture":"index_up","confidence":0.9}'
```
