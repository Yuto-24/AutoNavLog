#!/bin/sh
# WSL/Linux entry point. Use the operator's existing Python environment.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec "${AUTONAVLOG_OPS_PYTHON:-python3}" "$root/scripts/static_ops/app_release.py" "$@"
