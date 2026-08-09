#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import nbformat


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook", type=Path)
    args = parser.parse_args()
    notebook = nbformat.read(args.notebook, as_version=4)
    headings = [
        "開始・環境確認",
        "Projectの作成・読込",
        "コース取込・編集",
        "飛行計画入力",
        "Forecast Run選択",
        "計算実行",
        "警告・未確定項目の解消",
        "清書ビュー",
        "保存・Snapshot作成",
    ]
    markdown = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "markdown")
    missing = [heading for heading in headings if heading not in markdown]
    if missing:
        raise SystemExit(f"notebook is missing workflow headings: {missing}")
    for cell in notebook.cells:
        if cell.cell_type == "code" and (cell.outputs or cell.execution_count is not None):
            raise SystemExit("distribution notebook must not contain saved outputs")
    print(f"valid notebook: {args.notebook}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
