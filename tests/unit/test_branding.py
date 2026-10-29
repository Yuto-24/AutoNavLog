"""Display branding must not rename existing persistence or runtime identities."""

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_branding_preserves_distribution_and_cli_identifiers() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "autonavlog"
    assert project["scripts"] == {"autonavlog-web": "autonavlog.web.__main__:main"}
    assert json.loads((ROOT / "web/package.json").read_text())["name"] == "autonavlog-web"


def test_branding_preserves_owner_storage_identity() -> None:
    from autonavlog.storage.repository import owner_storage_key

    expected = hashlib.sha256(b"AutoNavLog OwnerProjectState v1\0branding-test-owner").hexdigest()
    assert owner_storage_key("branding-test-owner") == expected


def test_cli_displays_navmate_without_changing_storage_default() -> None:
    from autonavlog.web.__main__ import _parser

    parser = _parser()
    assert "Run the local NavMate Web application." in parser.format_help()
    assert parser.parse_args([]).storage_root == Path(".autonavlog-data")


def test_adopted_source_checksum() -> None:
    original = ROOT / "assets/branding/navmate-adopted-source.png"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == (
        "30faff87f7820ae4e62cc4c3b1bca016137b3af041672166472aaf5cf166c3e6"
    )


def test_icon_assets_are_available_in_supported_docker_build() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "COPY web/public ./web/public" in dockerfile
    assert "COPY assets/branding ./assets/branding" in dockerfile
    for exception in ("!web/public/*.png", "!assets/branding/*.png"):
        assert exception in dockerignore
        assert dockerignore.index(exception) > dockerignore.index("*.png")
