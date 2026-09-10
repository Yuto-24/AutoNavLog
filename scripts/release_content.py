"""Release metadata validation shared by CI."""

from __future__ import annotations

import re
from pathlib import Path

VERSION_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
VERSION_RE = re.compile(rf"^{VERSION_PATTERN}$")
RELEASE_RE = re.compile(rf"^## ({VERSION_PATTERN})$")
USER_SECTIONS = ("追加", "改善", "変更", "修正")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(value):
        raise ValueError("VERSION must be X.Y.Z")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def parse_version_source(source: str) -> str:
    value = source.removesuffix("\n")
    version_tuple(value)
    return value


def read_version(root: Path) -> str:
    return parse_version_source((root / "VERSION").read_text(encoding="utf-8"))


def parse_changelog(source: str) -> list[dict[str, object]]:
    releases: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    audience: str | None = None
    section: str | None = None
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.rstrip()
        match = RELEASE_RE.fullmatch(line)
        if match:
            version = match.group(1)
            if releases and version_tuple(version) >= version_tuple(str(releases[-1]["version"])):
                raise ValueError(f"line {line_number}: release versions must descend")
            current = {"version": version, "user": {}, "developer": []}
            releases.append(current)
            audience = section = None
            continue
        if line.startswith("## "):
            raise ValueError(f"line {line_number}: release header must be ## X.Y.Z")
        if not line.strip():
            continue
        if current is None:
            if line.startswith("# ") or not line.startswith("#"):
                continue
            raise ValueError(f"line {line_number}: invalid heading")
        if line == "### 利用者向け":
            if audience is not None:
                raise ValueError(f"line {line_number}: duplicate audience")
            audience, section = "user", None
        elif line == "### 開発者向け":
            if audience != "user":
                raise ValueError(f"line {line_number}: 開発者向け must follow 利用者向け")
            audience, section = "developer", None
        elif line.startswith("#### "):
            title = line[5:]
            user = current["user"]
            if audience != "user" or title not in USER_SECTIONS or title in user:
                raise ValueError(f"line {line_number}: invalid user section")
            if section is not None and USER_SECTIONS.index(title) <= USER_SECTIONS.index(section):
                raise ValueError(f"line {line_number}: user sections must follow the defined order")
            section = title
            user[section] = []
        elif re.fullmatch(r"- \S.*", line):
            if audience == "user" and section:
                if re.search(r"\bIssue\s*#?\s*\d+|#\d+", line, re.IGNORECASE):
                    raise ValueError(f"line {line_number}: Issue references belong in 開発者向け")
                user = current["user"]
                assert isinstance(user, dict)
                user.setdefault(section, []).append(line[2:])
            elif audience == "developer":
                developer = current["developer"]
                assert isinstance(developer, list)
                developer.append(line[2:])
            else:
                raise ValueError(f"line {line_number}: bullet has no section")
        elif audience == "developer" and line.startswith("  "):
            developer = current["developer"]
            assert isinstance(developer, list)
            if not developer:
                raise ValueError(f"line {line_number}: continuation has no bullet")
            developer[-1] += " " + line.strip()
        else:
            raise ValueError(f"line {line_number}: unsupported changelog content")
    if not releases:
        raise ValueError("CHANGELOG has no releases")
    for release in releases:
        user, developer = release["user"], release["developer"]
        if not isinstance(user, dict) or not user or not all(user.values()) or not developer:
            raise ValueError(
                f"release {release['version']} requires nonempty 利用者向け and 開発者向け"
            )
    return releases


def validate_sources(root: Path) -> str:
    version = read_version(root)
    releases = parse_changelog((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if releases[0]["version"] != version:
        raise ValueError("latest CHANGELOG version must match VERSION")
    return version
