#!/bin/sh
# Trusted container command, sent on stdin; candidate receives no provider credentials.
set -eu
git config --global --add safe.directory /app
python -m pip install 'setuptools>=69' wheel vendor/jma_gpv_weather-0.5.0-py3-none-any.whl '.[test]'
npm --prefix web ci
npm --prefix web exec -- playwright install --with-deps chromium
python scripts/validate_release.py
python scripts/validate_performance_data.py data/performance
python scripts/validate_runtime_data.py data
python scripts/export_schemas.py --check
npm --prefix web run typecheck
npm --prefix web run build:static
npm --prefix web run test:static-artifact
python -m http.server 4121 --bind 127.0.0.1 --directory web/dist-static > /tmp/static-server.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid"' EXIT
curl --fail --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:4121/ >/dev/null
npm --prefix web run test:static
# Only the hint directory/inventory need runner writes after the isolated build.
set -- web/dist-static/release.json web/dist-static/weather/msm
if [ -f web/dist-static/weather/msm/freshness.json ]; then
  set -- "$@" web/dist-static/weather/msm/freshness.json
fi
chown --no-dereference "$ARTIFACT_UID:$ARTIFACT_GID" "$@"
