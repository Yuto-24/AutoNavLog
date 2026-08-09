from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

SCRIPT_NAMESPACE = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "colab_e2e_assert.py"),
    run_name="colab_e2e_assert_test",
)
scan_output_notebook_errors = cast(
    Callable[[str | Path], list[dict[str, Any]]],
    SCRIPT_NAMESPACE["scan_output_notebook_errors"],
)
assert_output_notebook_clean = cast(
    Callable[[str | Path], None],
    SCRIPT_NAMESPACE["assert_output_notebook_clean"],
)


def _write_notebook(path: Path, outputs: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "markdown",
                        "metadata": {},
                        "source": ["# Test"],
                    },
                    {
                        "cell_type": "code",
                        "execution_count": 1,
                        "metadata": {},
                        "outputs": outputs,
                        "source": ["print('test')"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_output_notebook_scanner_accepts_clean_execution(tmp_path: Path) -> None:
    notebook = tmp_path / "clean_output.ipynb"
    _write_notebook(
        notebook,
        [{"output_type": "stream", "name": "stdout", "text": ["PASS\n"]}],
    )

    assert scan_output_notebook_errors(notebook) == []
    assert_output_notebook_clean(notebook)


def test_output_notebook_scanner_reports_cell_errors(tmp_path: Path) -> None:
    notebook = tmp_path / "failed_output.ipynb"
    _write_notebook(
        notebook,
        [
            {
                "output_type": "error",
                "ename": "RuntimeError",
                "evalue": "bootstrap failed",
                "traceback": ["RuntimeError: bootstrap failed"],
            }
        ],
    )

    errors = scan_output_notebook_errors(notebook)

    assert errors == [
        {
            "cell_index": 1,
            "code_cell_index": 1,
            "output_index": 0,
            "ename": "RuntimeError",
            "evalue": "bootstrap failed",
            "traceback": ["RuntimeError: bootstrap failed"],
        }
    ]
    with pytest.raises(RuntimeError, match="code cell 1: RuntimeError"):
        assert_output_notebook_clean(notebook)
