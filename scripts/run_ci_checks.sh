#!/bin/sh

set -eu

ruff check .
mypy src/autonavlog
pytest --cov=autonavlog --cov-report=term-missing
python scripts/validate_performance_data.py data/performance
python scripts/validate_runtime_data.py data
python scripts/export_schemas.py --check
python -m build
