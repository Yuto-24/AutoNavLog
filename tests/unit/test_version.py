import json
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

from autonavlog.version import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_version_matches_installed_package_metadata() -> None:
    assert __version__ == version("autonavlog")


def test_release_surfaces_are_synchronized() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "web/package-lock.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    expected_version = pyproject["project"]["version"]

    assert package["version"] == expected_version
    assert lock["version"] == expected_version
    assert lock["packages"][""]["version"] == expected_version
    current_versions = re.findall(
        r"^- 現在のバージョン: `([^`]+)`$", readme, re.MULTILINE
    )
    assert current_versions == [expected_version]
    release_versions = re.findall(
        r"^リリース時は `([^`]+)` が次の場所で一致していることを確認します。$",
        readme,
        re.MULTILINE,
    )
    assert release_versions == [expected_version]

    changelog_headings = list(
        re.finditer(
            r"^## (?P<version>\d+\.\d+\.\d+)(?:\s+-[^\n]*)?$",
            changelog,
            re.MULTILINE,
        )
    )
    assert changelog_headings
    latest_heading = changelog_headings[0]
    assert latest_heading.group("version") == expected_version
    latest_end = (
        changelog_headings[1].start() if len(changelog_headings) > 1 else len(changelog)
    )
    latest_section = changelog[latest_heading.start() : latest_end]
    subsection_headings = list(
        re.finditer(r"^### (?P<title>[^\n]+)$", latest_section, re.MULTILINE)
    )
    change_summary = None
    for index, subsection_heading in enumerate(subsection_headings):
        if subsection_heading.group("title").strip() == "配布":
            continue
        subsection_end = (
            subsection_headings[index + 1].start()
            if index + 1 < len(subsection_headings)
            else len(latest_section)
        )
        subsection_body = latest_section[subsection_heading.end() : subsection_end]
        if re.search(r"^-\s+\S", subsection_body, re.MULTILINE):
            change_summary = subsection_body
            break
    assert change_summary is not None
