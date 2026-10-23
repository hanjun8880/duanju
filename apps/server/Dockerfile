FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 8000
