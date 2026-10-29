FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# nginx(Debian 패키지, auth_request 포함)와 자체 서명 인증서용 openssl
RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx openssl \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY nginx/snippets/ /etc/nginx/snippets/
COPY pytest.ini start.sh ./
COPY app ./app
COPY tests ./tests
# Windows에서 CRLF로 체크아웃돼도 돌게 한다
RUN sed -i 's/\r$//' start.sh /etc/nginx/nginx.conf /etc/nginx/snippets/*.conf

# 443·80: nginx (바깥), 8001: 세션 API (docker-compose의 PORT, healthcheck가 /health로 확인)
EXPOSE 443 80 8001
CMD ["bash", "start.sh"]
