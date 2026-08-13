from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

NOTEBOOK = Path(__file__).parents[2] / "notebooks" / "AutoNavLog_Colab_Preview.ipynb"


def _document() -> dict[str, Any]:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code() -> str:
    return "\n".join(
        "".join(cell["source"]) for cell in _document()["cells"] if cell["cell_type"] == "code"
    )


def _markdown() -> str:
    return "\n".join(
        "".join(cell["source"]) for cell in _document()["cells"] if cell["cell_type"] == "markdown"
    )


def test_preview_notebook_is_clean_and_all_code_cells_parse() -> None:
    document = _document()

    assert document["nbformat"] == 4
    for cell in document["cells"]:
        if cell["cell_type"] != "code":
            continue
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        source = "".join(cell["source"])
        assert source.startswith("#@title ")
        ast.parse(source)


def test_preview_notebook_uses_one_verified_bundle_in_local_then_drive_order() -> None:
    code = _code()

    assert 'BUNDLE_NAME = f"autonavlog-colab-preview-{BUNDLE_VERSION}.zip"' in code
    assert '"53d66666d8e88e08d3996f9c41d1725470a8c2639c3d9d13d37820201d326596"' in code
    assert "BUNDLE_SHA256 == EXPECTED_BUNDLE_SHA256" in code
    assert 'LOCAL_BUNDLE = Path("/content") / BUNDLE_NAME' in code
    assert 'DRIVE_BUNDLE = Path("/content/drive/MyDrive") / BUNDLE_NAME' in code
    assert code.index("if LOCAL_BUNDLE.is_file()") < code.index('drive.mount("/content/drive")')
    assert code.index('drive.mount("/content/drive")') < code.index("BUNDLE = DRIVE_BUNDLE")
    assert "git" not in code.lower()


def test_preview_notebook_validates_and_safely_extracts_every_payload() -> None:
    code = _code()
    compact_code = "".join(code.split())

    for marker in (
        "PurePosixPath",
        "path traversal",
        "stat.S_ISLNK",
        "重複member",
        "MAX_MEMBER_SIZE",
        "MAX_TOTAL_SIZE",
        "bundle-manifest.json",
        "hashlib.sha256(content).hexdigest()",
        'set(info_by_name) == set(entries_by_name) | {"bundle-manifest.json"}',
        "tempfile.mkdtemp",
        "os.replace(staging, RUNTIME_ROOT)",
    ):
        assert marker in code
    assert 'terrain_member = "data/msm/terrain.npz"' in code
    assert 'weather_contract.get("qnh") == "MSM_ESTIMATED_QNH"' in code
    assert 'weather_contract.get("pzs_terrain_included") is True' in code
    assert 'weather_contract.get("terrain_path") == terrain_member' in code
    assert 'weather_contract.get("terrain_sha256")' in code
    assert 'entries_by_name[terrain_member]["sha256"]' in code
    assert 'descriptor.get("version") == expected_version' in code
    assert 'descriptor.get("size") == entries_by_name[wheel_name]["size"]' in code
    assert 'descriptor.get("sha256")==entries_by_name[wheel_name]["sha256"]' in compact_code


def test_preview_notebook_installs_pinned_wheels_and_real_runtime_components() -> None:
    code = _code()

    assert '"libeccodes0"' in code
    assert '"--force-reinstall"' in code
    assert 'importlib.metadata.version("autonavlog") == "0.2.1"' in code
    assert 'importlib.metadata.version("jma-msm-wind") == "0.2.1"' in code
    assert "ReferenceDataCatalogRepository" in code
    assert "AirportRepository.from_reference_catalog" in code
    assert "AirportRepository.from_csv" not in code
    assert "PerformanceRepository.from_directory" in code
    assert "performance.require_verified()" in code
    assert "MsmWeatherProvider(" in code
    assert 'terrain_cache_path=APP_DATA / "msm" / "terrain.npz"' in code
    assert "MsmMetarWeatherProvider" not in code
    assert 'LocalProjectRepository("/content/AutoNavLog-preview-data")' in code
    assert "app.render()" in code
    for eager_network_call in (
        ".resolve_run(",
        ".prepare_run(",
        ".query_batch(",
        "urlopen(",
        "requests.get(",
    ):
        assert eager_network_call not in code


def test_preview_notebook_has_no_embedded_kml_or_synthetic_runtime() -> None:
    text = NOTEBOOK.read_text(encoding="utf-8")

    assert "synthetic" not in text.lower()
    assert "fixture" not in text.lower()
    assert "FakeWeatherProvider" not in text
    assert "<kml" not in text.lower()
    assert "SYNTHETIC_PREVIEW" not in text
    for fixed_assignment in (
        "app.name.value",
        "app.pilot.value",
        "app.ship.value",
        "app.departure.value",
        "app.destination.value",
    ):
        assert fixed_assignment not in text


def test_preview_reader_caveats_describe_msm_estimated_qnh() -> None:
    markdown = _markdown()

    for required_text in (
        "地上準備",
        "非公式",
        "上空風・気温はMSM予報値",
        "MSM推定QNH",
        "公式飛行場気象の観測QNHではない",
        "原票と照合",
        "取得・算出できない場合",
        "手入力",
        "Pzs地形cache",
        "SEA・障害物評価",
        "各Pointの役割",
    ):
        assert required_text in markdown
    assert "METAR" not in markdown
