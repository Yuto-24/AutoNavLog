from pathlib import Path

import nbformat


def test_distribution_notebook_is_clean_and_thin() -> None:
    path = Path("notebooks/AutoNavLog.ipynb")
    notebook = nbformat.read(path, as_version=4)
    code_cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert all(not cell.outputs for cell in code_cells)
    assert all(cell.execution_count is None for cell in code_cells)
    assert all(cell.source.startswith("#@title ") for cell in code_cells)
    code = "\n".join(cell.source for cell in code_cells)
    assert "CalculationService" in code
    assert "PerformanceRepository.from_directory_for_application" in code
    assert "except ReferenceDataError:" in code
    assert "airports = AirportRepository([])" in code
    assert "solve_wind_triangle" not in code
    assert "pressure_altitude" not in code


def test_local_notebook_boot_cells_execute() -> None:
    notebook = nbformat.read(Path("notebooks/AutoNavLog.ipynb"), as_version=4)
    namespace: dict[str, object] = {}
    for cell in notebook.cells:
        if cell.cell_type == "code":
            exec(compile(cell.source, "AutoNavLog.ipynb", "exec"), namespace)
