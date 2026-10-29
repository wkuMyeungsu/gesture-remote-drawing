# security

HTTPS·경로별 라우팅(nginx 443)과 QR 세션 발급·검증(세션 API 8001). 구조는 `docs/architecture.html`, 메시지 형식은 `docs/contracts/README.md`.

## 실행 (Windows)

`start-windows.bat`을 더블클릭한다. PC 내부 IP를 찾아 `.env`의 `SECURITY_CERT_HOST`에 넣고, 전체 서비스를 띄운 뒤 브라우저를 연다. Wi-Fi가 바뀌어 IP가 달라지면 인증서도 다시 만든다.

- PC: `http://localhost` — 인증서 경고 없이 열린다 (PC 안에서만 오가는 연결)
- 휴대폰: 같은 Wi-Fi에서 PC 화면의 QR을 찍는다. 처음 한 번 인증서 경고가 나오면 계속 진행한다
  - iPhone: 세부사항 보기 → 이 웹 사이트 방문 / Android: 고급 → 계속 진행
  - 휴대폰 카메라는 HTTPS에서만 켜지고, PC 내부 IP에는 공인 인증서를 받을 수 없어서 이 경고는 피할 수 없다

처음 한 번만: Docker Desktop 설치, Windows 방화벽에서 443 인바운드 허용
(관리자 PowerShell: `New-NetFirewallRule -DisplayName "gesture 443" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow`)

## 경로

| 경로 | 보내는 곳 | 인증 |
|---|---|---|
| `/`, `/ws/board` | web | 없음 |
| `/api/session` | 세션 API | 없음 (발급 횟수 제한) |
| `/m/` | stream | 1회용 토큰 → `sid` 쿠키 발급 |
| `/ws/stream` | stream | `sid` 쿠키 |
| `/actions`, `/events` | — | 바깥에서는 404 (내부 전용) |

## 테스트

```bash
docker compose run --rm --no-deps security pytest -q
```
