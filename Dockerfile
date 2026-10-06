# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install --prefix=/install . && rm -rf app

FROM base AS runtime
ARG VERSION=dev
LABEL org.opencontainers.image.source="https://github.com/Tudolin/findcar" \
      org.opencontainers.image.description="carwatch — monitor de carros usados" \
      org.opencontainers.image.version="${VERSION}"
COPY --from=deps /install /usr/local
# Headless Chromium + its system libraries (OLX only serves real browsers).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/*
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN useradd --system --uid 10001 --home /data carwatch && mkdir -p /data && chown carwatch /data
USER carwatch
ENV DATA_DIR=/data APP_VERSION=${VERSION}
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# One worker on purpose: the scheduler lives in this process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--no-access-log"]
