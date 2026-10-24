# syntax=docker/dockerfile:1
FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.rst ./
COPY amagama amagama
RUN pip wheel --wheel-dir /wheels ".[recommended]" gunicorn


FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 amagama

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

WORKDIR /app
COPY wsgi.py ./
COPY docker/settings.py ./docker-settings.py

ENV AMAGAMA_CONFIG=/app/docker-settings.py
USER amagama
EXPOSE 8888

CMD ["gunicorn", "--bind", "0.0.0.0:8888", "wsgi:application"]
