#!/usr/bin/env bash
# 컨테이너 시작점: 인증서 준비 → 세션 API(PORT, 기본 8001) → nginx(443, 80).
# 둘 중 하나가 죽으면 컨테이너도 끝내서 docker가 다시 띄우게 한다.
set -euo pipefail

# services/security/certs/를 docker-compose가 여기에 연결한다. mkcert로 만든 파일을 cert.pem·key.pem으로 넣어도 된다
CERT_DIR=/etc/nginx/certs
if [[ ! -f $CERT_DIR/cert.pem || ! -f $CERT_DIR/key.pem ]]; then
    host="${SECURITY_CERT_HOST:-localhost}"  # PC 내부 IP (예: 192.168.0.12)
    if [[ $host =~ ^[0-9.]+$ ]]; then san="IP:$host"; else san="DNS:$host"; fi
    san="$san,DNS:localhost,IP:127.0.0.1"
    echo "인증서가 없어 자체 서명 인증서를 만든다 ($san). IP가 바뀌면 certs/의 두 파일을 지우고 다시 띄운다."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=$host" -addext "subjectAltName=$san" \
        -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" 2>/dev/null
fi

nginx -t
python -m app &
nginx -g 'daemon off;' &
wait -n
exit 1
