from pathlib import Path

import nbformat


def test_distribution_notebook_is_clean_and_thin() -> None:
    path = Path("notebooks/AutoNavLog.ipynb")
    notebook = nbformat.read(path, as_version=4)
    assert all(not cell.outputs for cell in notebook.cells if cell.cell_type == "code")
    code = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "code")
    assert "CalculationService" in code
    assert "solve_wind_triangle" not in code
    assert "pressure_altitude" not in code


def test_local_notebook_boot_cells_execute() -> None:
    notebook = nbformat.read(Path("notebooks/AutoNavLog.ipynb"), as_version=4)
    namespace: dict[str, object] = {}
    for cell in notebook.cells:
        if cell.cell_type == "code":
            exec(compile(cell.source, "AutoNavLog.ipynb", "exec"), namespace)
