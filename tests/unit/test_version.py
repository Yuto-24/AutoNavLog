import json
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

from autonavlog.version import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_version_matches_installed_package_metadata():
    assert __version__ == version("autonavlog")
    assert __version__ == (ROOT / "VERSION").read_text().strip()


def test_version_is_the_only_release_version_source():
    expected = (ROOT / "VERSION").read_text().strip()
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["setuptools"]["dynamic"]["version"] == {"file": ["VERSION"]}
    assert "version" not in json.loads((ROOT / "web/package.json").read_text())
    lock = json.loads((ROOT / "web/package-lock.json").read_text())
    assert "version" not in lock and "version" not in lock["packages"][""]
    headers = re.findall(r"^## (.+)$", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)
    assert headers[0] == expected
    assert "現在のバージョン:" not in (ROOT / "README.md").read_text()
