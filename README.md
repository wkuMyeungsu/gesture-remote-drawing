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
- 서비스별 설명·환경 변수·테스트: `services/<서비스>/README.md`

## 실행

필요한 것: Docker Desktop (실행 중일 것), Docker Compose 2.24 이상.

### 한 번에 (Windows)

`services/security/start-windows.bat`을 더블클릭한다. PC IP를 `.env`에 넣고, 전체를 띄우고, 브라우저를 연다.

1. PC: `http://localhost`에 그림판과 QR이 뜬다.
2. 휴대폰: 같은 Wi-Fi에서 QR을 찍는다 → 인증서 경고는 "계속" → **촬영 시작**.
3. PC 화면이 "휴대폰 연결됨"으로 바뀌면 검지를 들어 그린다.

처음 한 번만: Windows 방화벽에서 443 인바운드 허용 (관리자 PowerShell)

```powershell
New-NetFirewallRule -DisplayName "gesture 443" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

### 직접

```bash
docker compose up --build -d           # 전체
docker compose up --build web          # 서비스 하나만
docker compose ps                      # 상태 (5개 모두 healthy)
docker compose down                    # 종료
```

- `.env`가 없거나 `SECURITY_CERT_HOST`가 비어 있으면 QR이 `localhost`로 나와 휴대폰이 접속하지 못한다. `.env.example`을 `.env`로 복사해 PC 내부 IP(`ipconfig`의 Wi-Fi IPv4)를 넣는다.
- IP를 바꿨으면 `services/security/certs/`의 `cert.pem`·`key.pem`을 지우고 security를 다시 띄운다.
- 호스트 포트가 다른 프로그램과 겹치면 `.env`에서 포트만 바꾼다.
- 컨테이너 안에서 다른 서비스는 `localhost`가 아니라 서비스 이름으로 부른다 (`http://vision:8003`).

### 연결 확인

| 보고 싶은 것 | 주소 |
|---|---|
| 서비스 상태 | `docker compose ps` |
| 세션별 프레임 수·버린 수·처리 시간 | `http://localhost:8002/sessions` (stream) |
| 손을 찾은 수·gesture 전송 결과 | `http://localhost:8003/stats` (vision) |
| 인식한 동작·기능·지연 | PC 그림판 위쪽 표시줄 (web) |

앞 단계 없이 내 파트만 확인하는 도구: `services/stream/dev/fake_vision.py`, `services/vision/dev/try_image.py`, `services/web/dev/fake_gesture.py`.

## 브랜치

- `main`: 최종본. `dev`: 통합. 둘 다 직접 push하지 않고 PR로만 합친다.
- 작업 브랜치: `<태그>/<파트>/<기능>` — 태그는 `feature` `fix` `docs` `refactor` `chore`, 파트는 `web` `security` `stream` `vision` `gesture` (여러 파트에 걸치면 `common`).
  예: `feature/web/qr-pairing`, `fix/vision/no-hand`
- 작업 브랜치는 `dev`에서 만들고 `dev`로 PR을 올린다. 병합 후 삭제한다.
- 커밋 메시지: `<타입>(<파트>): <요약>`. 예: `feat(web): QR 페어링 화면 추가`
- PR 병합은 Create a merge commit만 사용한다.
- `.env`, 비밀키, 인증서(`*.pem` `*.key` `*.crt`), 모델 가중치(`*.pt` `*.onnx`)는 커밋하지 않는다.
