# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend

WORKDIR /build
COPY web/package.json web/package-lock.json ./web/
RUN npm --prefix web ci
COPY web ./web
RUN npm --prefix web run build


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/autonavlog

RUN groupadd --system --gid 10001 autonavlog \
    && useradd --system --uid 10001 --gid 10001 \
        --home-dir /var/lib/autonavlog --shell /usr/sbin/nologin autonavlog \
    && mkdir -p /var/lib/autonavlog \
    && chown autonavlog:autonavlog /var/lib/autonavlog

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
COPY vendor ./vendor
COPY --from=frontend /build/web/dist ./src/autonavlog/web/static

RUN chmod -R a=rX /opt/autonavlog \
    && python -m pip install vendor/jma_msm_wind-0.2.1-py3-none-any.whl .

USER autonavlog

EXPOSE 8000
VOLUME ["/var/lib/autonavlog"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

ENTRYPOINT ["autonavlog-web"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--data-root", "/opt/autonavlog/data", "--storage-root", "/var/lib/autonavlog", "--weather", "msm", "--maximum-sessions", "256"]
