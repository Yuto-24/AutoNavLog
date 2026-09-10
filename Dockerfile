# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS release-tools

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["python", "scripts/validate_release.py"]

FROM node:22-bookworm-slim AS frontend

WORKDIR /build
COPY web/package.json web/package-lock.json ./web/
RUN npm --prefix web ci
COPY VERSION CHANGELOG.md KNOWN_ISSUES.md ./
COPY web ./web
RUN npm --prefix web run build


FROM python:3.12-slim-bookworm AS python-base

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

COPY pyproject.toml VERSION README.md ./
COPY src ./src
COPY data ./data
COPY vendor ./vendor
COPY --from=frontend /build/web/dist ./src/autonavlog/web/static

FROM python-base AS test

COPY tests ./tests
COPY scripts ./scripts
COPY VERSION CHANGELOG.md KNOWN_ISSUES.md Dockerfile .dockerignore ./
COPY web/package.json web/package-lock.json ./web/
COPY .github/workflows/test.yml ./.github/workflows/test.yml

RUN chmod 755 scripts/run_ci_checks.sh \
    && python -m pip install vendor/jma_msm_wind-0.2.1-py3-none-any.whl ".[test]" \
    && rm -rf build dist src/autonavlog.egg-info

CMD ["/opt/autonavlog/scripts/run_ci_checks.sh"]


FROM python-base AS runtime

RUN chmod -R a=rX /opt/autonavlog \
    && python -m pip install vendor/jma_msm_wind-0.2.1-py3-none-any.whl . \
    && rm -rf build dist src/autonavlog.egg-info

USER autonavlog

EXPOSE 8000
VOLUME ["/var/lib/autonavlog"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

ENTRYPOINT ["autonavlog-web"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--data-root", "/opt/autonavlog/data", "--storage-root", "/var/lib/autonavlog", "--weather", "msm", "--maximum-sessions", "256"]
