# ╔══════════════════════════════════════════════════════════════╗
# ║     docker-socket-watchdog — Lightweight Alpine Container   ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Build:
#   docker build -t watchdog .
#
# Run:
#   docker run -d \
#     --name watchdog \
#     --restart unless-stopped \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     --env-file .env \
#     watchdog
#

# ── Builder stage (install deps, then discard build tools) ──
FROM python:3.12-alpine AS builder

WORKDIR /build

COPY requirements.txt .

# Install build dependencies needed for some Python packages, then pip install
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && apk del .build-deps

# ── Runtime stage (minimal) ──
FROM python:3.12-alpine

LABEL maintainer="QADRI"
LABEL description="docker-socket-watchdog — Automated Docker Service Healer"
LABEL org.opencontainers.image.source="https://github.com/QADRI/docker-socket-watchdog"

# Don't generate .pyc files, don't buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only the installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (order: least-changed first for layer caching)
COPY config.yaml .
COPY main.py .
COPY sentinel/ ./sentinel/

# Create logs directory
RUN mkdir -p /app/logs

# Health check: verify Docker socket is accessible
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=10s \
    CMD python -c "import docker; docker.from_env().ping()" || exit 1

ENTRYPOINT ["python", "main.py"]
CMD ["--watch-only"]
