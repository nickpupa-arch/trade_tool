# Single-stage Python image for the Fly.io orchestrator.
# Slim base (~50MB); pandas / numpy / pyarrow / cryptography all have manylinux
# wheels for cp311 so no system build tools are needed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so the heavy layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app. .dockerignore excludes _site/, .cache/, *.enc, etc.
COPY . .

# Point state at the mounted volume by default. Local docker runs without the
# volume can override with -e STATE_PATH=/tmp/state.enc.
ENV STATE_PATH=/data/state.enc \
    PORT=8080

EXPOSE 8080

# Workers + HTTP server in one process; Fly sends SIGTERM on deploy.
CMD ["python", "loop.py"]
