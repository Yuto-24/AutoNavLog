"""Generated Local assets must not depend on an earlier build."""
import json
import runpy
from pathlib import Path


def test_prepare_local_replaces_generated_tree(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "scripts/prepare_local.py"
    script.parent.mkdir()
    script.write_text((root / "scripts/prepare_local.py").read_text())
    for name in ("pyproject.toml", "VERSION", "README.md"):
        (tmp_path / name).write_text("1.20.0")
    for name in ("src/autonavlog", "data/performance", "data/reference", "vendor", "web/public"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "web/package.json").write_text(json.dumps({"dependencies": {"pyodide": "0.27.7"}}))
    (tmp_path / "vendor/jma_gpv_weather-0.6.0-py3-none-any.whl").write_bytes(b"vendor")
    (tmp_path / "web/public/current.txt").write_text("current")
    stale = tmp_path / "web/public-local"
    stale.mkdir()
    (stale / "_redirects").write_text("/* https://old.example 302")
    (stale / "removed.txt").write_text("old")
    monkeypatch.delenv("AUTONAVLOG_MSM_FEED", raising=False)
    monkeypatch.delenv("AUTONAVLOG_GSM_FEED", raising=False)
    monkeypatch.delenv("AUTONAVLOG_TEST_FIXTURES", raising=False)

    def build(command, **kwargs):
        Path(command[-1], "autonavlog-1.20.0-py3-none-any.whl").write_bytes(b"wheel")

    monkeypatch.setattr("subprocess.run", build)
    runpy.run_path(str(script))
    assert not (stale / "_redirects").exists()
    assert not (stale / "removed.txt").exists()
    assert (stale / "current.txt").read_text() == "current"
    manifest = json.loads((stale / "local/manifest.json").read_text())
    assert manifest["version"] == "1.20.0"
    assert manifest["pyodideVersion"] == "0.27.7"
