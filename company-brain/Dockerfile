# Flowithm FastAPI backend image. Built by Railway (rootDirectory =
# company-brain). The Slack bot is a separate process — see
# slack/app.py — and is NOT included in this image's CMD.

FROM python:3.11-slim

WORKDIR /app

# System deps. gcc is needed for source builds of bcrypt on the slim
# image; remove the apt cache after install to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first — this layer caches when only app code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project. .dockerignore keeps env files, secrets,
# node_modules, the UI tree, and dev artifacts out.
COPY . .

# Run as a non-root user. uid 1000 matches Railway's default expectations
# and means file ownership lines up with most host filesystems.
RUN useradd -m -u 1000 flowithm
USER flowithm

EXPOSE 8000

# TODO: increase --workers when the in-memory rate limiter (api/auth.py)
# moves to Redis. Right now each worker has its own counter, so N workers
# = N * 100 req/min effective limit per key.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
