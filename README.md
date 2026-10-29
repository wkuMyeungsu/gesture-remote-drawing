# 실시간 손동작 인식 원격 그림판

휴대폰 카메라로 손을 찍으면 PC 화면의 그림판에 그림이 그려지는 웹 서비스.
QR로 기기를 연결하고, 휴대폰이 보낸 영상에서 손 위치를 찾아 그리기·제어 명령으로 바꾼다.

## 흐름

1. 기기 페어링 — PC 화면의 QR을 휴대폰으로 스캔
2. 실시간 영상 스트리밍 — 휴대폰 카메라 영상을 서버로 전송
3. 원격 드로잉 및 제어 — 손 움직임에 따라 PC 그림판에 그림

```
휴대폰 ─HTTPS─▶ [security] ─▶ [stream] ─▶ [vision] ─▶ [gesture] ─▶ [web]
PC 브라우저 ─HTTPS─▶ [security] ─▶ [web]
```

## 구조 · 담당 · 포트

| 폴더 | 역할 | 담당 | 포트 | 컨테이너 주소 |
|---|---|---|---|---|
| `services/web/` | PC 그림판 페이지, QR 표시, 기능(`action`) 실행 | 박명수 | 8000 | `http://web:8000` |
| `services/security/` | nginx HTTPS·라우팅, QR 세션 발급·검증 | 김도은 | 443, 8001 | `http://security:8001` |
| `services/stream/` | 휴대폰 카메라 페이지, 프레임 수신, vision으로 전달 | 임우진 | 8002 | `http://stream:8002` |
| `services/vision/` | 전처리, MediaPipe Hands로 손 좌표 21개 추출 | 이진아 | 8003 | `http://vision:8003` |
| `services/gesture/` | 좌표 보정, 동작(`gesture`) 판별, 동작 → 기능(`action`) 대응 | 김대희 | 8004 | `http://gesture:8004` |

- 전체 구조와 연결: `docs/architecture.html`
- 서비스 사이에 주고받는 데이터 형식: `docs/contracts/README.md`

## 실행

필요한 것: Docker Desktop (실행 중일 것), Docker Compose 2.24 이상.

```bash
docker compose up --build              # 전체
docker compose up --build web          # 서비스 하나만
docker compose down                    # 종료
docker compose ps                      # 상태 (healthy 확인)
```

- 현재 `services/*`는 `/health`만 답하는 뼈대 서버다. 담당자가 자기 폴더의 `main.py`와 `Dockerfile`을 바꿔 가며 채운다.
- 호스트 포트가 다른 프로그램과 겹치면 `.env.example`을 `.env`로 복사해 포트만 바꾼다.
- 컨테이너 안에서 다른 서비스는 `localhost`가 아니라 서비스 이름으로 부른다 (`http://vision:8003`).
- 휴대폰 브라우저에서 카메라(`getUserMedia`)를 쓰려면 페이지가 HTTPS여야 한다 (`localhost` 제외).

## 브랜치

- `main`: 최종본. `dev`: 통합. 둘 다 직접 push하지 않고 PR로만 합친다.
- 작업 브랜치: `<태그>/<파트>/<기능>` — 태그는 `feature` `fix` `docs` `refactor` `chore`, 파트는 `web` `security` `stream` `vision` `gesture` (여러 파트에 걸치면 `common`).
  예: `feature/web/qr-pairing`, `fix/vision/no-hand`
- 작업 브랜치는 `dev`에서 만들고 `dev`로 PR을 올린다. 병합 후 삭제한다.
- 커밋 메시지: `<타입>(<파트>): <요약>`. 예: `feat(web): QR 페어링 화면 추가`
- PR 병합은 Create a merge commit만 사용한다.
- `.env`, 비밀키, 인증서(`*.pem` `*.key` `*.crt`), 모델 가중치(`*.pt` `*.onnx`)는 커밋하지 않는다.
