"""세션 API 시작점: python -m app (포트는 docker-compose의 PORT 환경변수). nginx는 start.sh가 같이 띄운다."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), log_level="info")
