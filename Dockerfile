# Safe Line — production Docker image: the API and the built web app in ONE container
#
# Stage 1 builds the React app (served by the API at `/`, the API stays at `/v1`).
# Stage 2: python:3.12-slim + Tesseract Hebrew (local fallback engine) + OpenCV runtime deps.
# Non-root user (uid 1000, which is also the uid a Hugging Face Space runs as).
# DATA_DIR=/tmp/safeline — nothing survives a restart (privacy by design).
# One uvicorn worker: concurrency comes from the async queue, not from processes.
#
# Build:  docker build -t safeline .
# Run:    docker run --rm -p 8000:8000 --env-file .env safeline
# Health: curl localhost:8000/v1/health
# Verify: docker run --rm safeline tesseract --list-langs  # must show 'heb'

FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY fixtures/ /fixtures/
# Same origin as the API: no CORS, no second host. The demo mock client is not used.
ENV VITE_API_BASE=/v1
RUN npx vite build

FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="Safe Line" \
      org.opencontainers.image.description="Confidence-aware extraction engine for Israeli medical commitment forms" \
      org.opencontainers.image.version="0.1.0"

# --- System dependencies ---------------------------------------------------
# tesseract-ocr-heb: Hebrew language pack - required; without it /v1/health answers 503 and uploads are refused
# libgl1 libglib2.0-0: OpenCV headless runtime
# libzbar0: barcode decoding runtime (zxing-cpp may need it on some platforms)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-heb \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# --- Non-root user ---------------------------------------------------------
RUN groupadd --gid 10001 safeline \
 && useradd --uid 10001 --gid safeline --no-create-home --shell /sbin/nologin safeline

# --- Application -----------------------------------------------------------
WORKDIR /app

# Copy and install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY pyproject.toml .
COPY --from=web /web/dist ./frontend/dist

# --- Data directory --------------------------------------------------------
# Nothing survives a restart — this is the privacy guarantee.
ENV DATA_DIR=/tmp/safeline
ENV STATIC_DIR=/app/frontend/dist
RUN mkdir -p /tmp/safeline && chown safeline:safeline /tmp/safeline && chmod 700 /tmp/safeline

# --- Switch to non-root user -----------------------------------------------
USER safeline

# --- Runtime environment ---------------------------------------------------
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TESSERACT_CMD=/usr/bin/tesseract
ENV LOG_LEVEL=INFO

# --- Health check ----------------------------------------------------------
# Render free tier keeps the container alive; the health check path is also
# used by UptimeRobot and by the deploy smoke test.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/v1/health' % os.environ.get('PORT', '8000'))" \
    || exit 1

# --- Expose and start ------------------------------------------------------
EXPOSE 8000

# Render injects PORT; locally it is 8000. --proxy-headers makes the client address seen by the
# rate limiter the real one behind Render's proxy (trust is set by FORWARDED_ALLOW_IPS, see
# render.yaml). Single worker: concurrency comes from the async job queue (MAX_CONCURRENCY),
# not from multiple uvicorn processes.
CMD ["sh", "-c", "exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
