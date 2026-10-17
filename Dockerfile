FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RAE_LOCAL_DIR=/tmp/rae
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rae ./rae
COPY web ./web

# Run as an unprivileged user. Nothing is written inside /app (local mode writes to /tmp/rae).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin rae
USER rae

# Cloud Run sets PORT. One worker keeps memory low; concurrency is handled by asyncio.
CMD ["sh", "-c", "exec uvicorn rae.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --timeout-keep-alive 75"]
