#!/bin/sh

set -eu

python scripts/validate_release.py
ruff check .
mypy src/autonavlog
python -m pytest -p scripts.pytest_shard --cov=autonavlog --cov-report=term-missing --durations=30
python scripts/validate_performance_data.py data/performance
python scripts/validate_runtime_data.py data
python scripts/export_schemas.py --check
python -m build
