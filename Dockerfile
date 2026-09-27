# syntax=docker/dockerfile:1
# VL-YOLO-Anchor backend image (FastAPI). CPU-only: VL/LLM inference is delegated
# to a configurable remote OpenAI-compatible endpoint, so no CUDA/torch/weights
# are baked in.

# --- Build stage: resolve runtime-only Python deps into a wheel tree ---
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN pip install --no-cache-dir uv
# Lockfile + metadata first for layer caching.
COPY pyproject.toml uv.lock README.md ./
# Export the locked runtime dependencies (no dev, no optional extras) and
# install them into an isolated prefix we can copy into the runtime image.
RUN uv export --frozen --no-dev --no-annotate -o requirements.txt \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Runtime stage ---
FROM python:3.12-slim
RUN groupadd -r app && useradd -r -g app -d /app -s /usr/sbin/nologin app
WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=app:app src ./src
COPY --chown=app:app config ./config
COPY --chown=app:app prompts ./prompts
COPY --chown=app:app run.py ./

# Writable location for tasks/ and logs/.
RUN mkdir -p /data && chown -R app:app /data
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VL_ANCHOR_HOST=0.0.0.0 \
    VL_ANCHOR_PORT=8765 \
    VL_ANCHOR_DATA_DIR=/data \
    VL_ANCHOR_CONFIG_DIR=/app/config

EXPOSE 8765
USER app

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/health')" || exit 1

# exec-form via sh so VL_ANCHOR_HOST/PORT env overrides the bind and uvicorn
# becomes PID 1 (receives SIGTERM for graceful shutdown).
CMD ["sh", "-c", "exec python -m uvicorn src.api_server:app --host ${VL_ANCHOR_HOST:-0.0.0.0} --port ${VL_ANCHOR_PORT:-8765}"]
