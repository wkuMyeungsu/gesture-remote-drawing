FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GLOG_minloglevel=2 \
    TF_CPP_MIN_LOG_LEVEL=2

# mediapipe 바이너리가 쓰는 시스템 라이브러리 (slim 이미지에 없을 수 있음)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-mediapipe.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt \
    && pip install --no-deps -r requirements-mediapipe.txt

COPY pytest.ini ./
COPY app ./app
COPY tests ./tests

# 포트는 docker-compose.yml의 PORT(8003)를 따른다
CMD ["python", "-m", "app"]
