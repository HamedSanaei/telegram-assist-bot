# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12.10-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY config/configuration.example.json config/configuration.example.json
RUN uv sync --locked --group dev --no-install-project

COPY src src
RUN uv build --no-build-isolation \
    && uv sync --locked --no-dev --no-install-project \
    && uv pip install --python .venv/bin/python --no-deps \
       dist/telegram_assist_bot-*.whl

FROM python:3.12.10-slim-bookworm AS runtime

ARG VERSION=1.0.0
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="Telegram Assist Bot" \
      org.opencontainers.image.description="Telegram channel administration assistant" \
      org.opencontainers.image.source="https://github.com/HamedSanaei/telegram-assist-bot" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="Proprietary"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TAB_CONFIG_PATH="/app/config/configuration.json"

RUN groupadd --gid 10001 telegram-assist \
    && useradd --uid 10001 --gid 10001 --create-home \
       --home-dir /home/telegram-assist telegram-assist \
    && mkdir -p /app/config /app/var/media /app/var/sessions \
    && chown -R 10001:10001 /app /home/telegram-assist

WORKDIR /app
COPY --from=builder --chown=10001:10001 /build/.venv /app/.venv

USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "telegram_assist_bot"]
CMD ["check", "--config", "/app/config/configuration.json"]
