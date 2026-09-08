"""Strict, dependency-free release source validation shared by CI and release planning."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime
from pathlib import Path

SECTIONS = ("追加", "改善", "変更", "修正")
VERSION = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
HEADER = re.compile(rf"^## ({VERSION}) - (\d{{4}}-\d{{2}}-\d{{2}}(?: \d{{2}}:\d{{2}} JST)?)$")
META = re.compile(r"<!-- ([a-z-]+): (.+) -->")


def version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(VERSION, value):
        raise ValueError(f"Invalid version: {value!r}; expected X.Y.Z")
    return tuple(map(int, value.split(".")))


def date_value(value: str) -> str:
    fmt = "%Y-%m-%d %H:%M JST" if " JST" in value else "%Y-%m-%d"
    parsed = datetime.strptime(value, fmt)
    if parsed.strftime(fmt) != value:
        raise ValueError(f"Invalid release date: {value}")
    return value


def histories(source: str, *, user: bool) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    section: str | None = None
    section_count = 0
    previous_section = -1
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        header = HEADER.fullmatch(line)
        if header:
            if user and entries and (section is None or not section_count):
                raise ValueError(f"line {number}: empty release/section")
            version, date = header.groups()
            date_value(date)
            if entries and version_tuple(version) >= version_tuple(entries[-1][0]):
                raise ValueError(f"line {number}: release versions must descend without duplicates")
            entries.append((version, date))
            section, section_count, previous_section = None, 0, -1
        elif line.startswith("## "):
            raise ValueError(f"line {number}: invalid release header")
        elif not entries:
            if line.startswith("# "):
                continue
            if user or line.startswith("#"):
                raise ValueError(f"line {number}: unexpected content before releases")
        elif user:
            if line.startswith("### "):
                title = line[4:]
                if title not in SECTIONS or SECTIONS.index(title) <= previous_section:
                    raise ValueError(
                        f"line {number}: invalid/duplicate/out-of-order section {title}"
                    )
                if section is not None and not section_count:
                    raise ValueError(f"line {number}: empty section")
                section, section_count, previous_section = title, 0, SECTIONS.index(title)
            elif section and re.fullmatch(r"- \S.*", line):
                if re.search(r"(?:Issue\s*#?\s*\d+|#\d+)", line, re.I):
                    raise ValueError(f"line {number}: Issue numbers are developer metadata")
                section_count += 1
            else:
                raise ValueError(
                    f"line {number}: user notes require sections and single-line bullets"
                )
    if not entries or (user and (section is None or not section_count)):
        raise ValueError("No releases or empty final release/section")
    return entries


def parse_fragment(source: str) -> dict:
    metadata: dict[str, str] = {}
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        match = META.fullmatch(line)
        if match and current is None:
            key, value = match.groups()
            if key not in ("section", "issue", "user-visible") or key in metadata:
                raise ValueError(f"line {number}: unknown or duplicate metadata {key}")
            metadata[key] = value
        elif line in ("## Developer", "## User"):
            current = line[3:]
            if current in bodies or (current == "User" and "Developer" not in bodies):
                raise ValueError(f"line {number}: duplicate or out-of-order body")
            bodies[current] = []
        elif current and re.fullmatch(r"- \S.*", line):
            bodies[current].append(line[2:])
        else:
            raise ValueError(f"line {number}: invalid fragment structure")
    if metadata.get("section") not in SECTIONS:
        raise ValueError("fragment requires a valid section")
    if metadata.get("user-visible", "true") not in ("true", "false"):
        raise ValueError("user-visible must be true or false")
    issue = metadata.get("issue")
    if issue is not None and not re.fullmatch(r"[1-9]\d*", issue):
        raise ValueError("issue must be a positive integer")
    if not bodies.get("Developer"):
        raise ValueError("Developer bullets are required")
    mentioned = re.findall(r"Issue\s+#(\d+)", " ".join(bodies["Developer"]), re.I)
    if mentioned and (issue is None or any(value != issue for value in mentioned)):
        raise ValueError("issue metadata must match the referenced Issue")
    visible = metadata.get("user-visible", "true") == "true"
    if visible and not bodies.get("User"):
        raise ValueError("User bullets are required unless user-visible is false")
    if not visible and "User" in bodies:
        raise ValueError("user-visible false must omit User")
    if "User" in bodies:
        histories(
            "# Notes\n## 0.0.0 - 2000-01-01\n### "
            + metadata["section"]
            + "\n"
            + "\n".join("- " + text for text in bodies["User"]),
            user=True,
        )
    return {
        "section": metadata["section"],
        "developer": bodies["Developer"],
        "user": bodies.get("User", []),
    }


def parse_known_issues(source: str) -> list[dict]:
    issues: list[dict] = []
    pending: dict[str, str] = {}
    current: dict | None = None
    subsection: str | None = None
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        if re.fullmatch(r"<!-- [^:]* -->", line):
            continue  # Explanatory comments carry no metadata or visible content.
        match = META.fullmatch(line)
        if match:
            key, value = match.groups()
            if key not in ("id", "github-issue") or key in pending:
                raise ValueError(f"line {number}: unknown or duplicate metadata")
            if key == "id":
                if pending or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
                    raise ValueError(f"line {number}: invalid id or metadata order")
                current, subsection = None, None
            elif "id" not in pending or not re.fullmatch(r"[1-9]\d*", value):
                raise ValueError(
                    f"line {number}: github-issue must follow id and be a positive integer"
                )
            pending[key] = value
        elif line == "# 既知の不具合" and not issues and not pending:
            continue
        elif line.startswith("## "):
            if "id" not in pending or not line[3:].strip():
                raise ValueError(f"line {number}: issue requires id and title")
            current = {"id": pending["id"], "title": line[3:], "description": [], "sections": []}
            if any(item["id"] == current["id"] for item in issues):
                raise ValueError(f"line {number}: duplicate issue id")
            issues.append(current)
            pending, subsection = {}, None
        elif current and line.startswith("### "):
            subsection = line[4:]
            if subsection not in ("影響する条件", "回避方法") or any(
                item["title"] == subsection for item in current["sections"]
            ):
                raise ValueError(f"line {number}: unknown or duplicate issue section")
            current["sections"].append({"title": subsection, "items": []})
        elif current and subsection and re.fullmatch(r"- \S.*", line):
            current["sections"][-1]["items"].append(line[2:])
        elif current and not subsection and not re.match(r"\s*(?:[#<>`~*+|]|- |\d+\. )", line):
            current["description"].append(line)
        else:
            raise ValueError(f"line {number}: invalid Known Issue structure")
    if pending:
        raise ValueError("metadata without an issue title/body")
    for issue in issues:
        if not issue["description"] or any(not s["items"] for s in issue["sections"]):
            raise ValueError("Known Issue requires description and nonempty optional sections")
    return issues


def fragment_paths(root: Path) -> list[Path]:
    paths = sorted((root / "changes").iterdir())
    for path in paths:
        if path.name != "README.md" and (
            not path.is_file() or path.suffix != ".md" or path.is_symlink()
        ):
            raise ValueError(f"Unexpected changes entry: {path.name}")
    return [path for path in paths if path.name != "README.md"]


def validate_sources(files: dict[str, str]) -> str:
    developer = histories(files["CHANGELOG.md"], user=False)
    user = histories(files["RELEASE_NOTES.md"], user=True)
    if developer != user:
        raise ValueError("CHANGELOG / RELEASE_NOTES version order and dates must match")
    parse_known_issues(files["KNOWN_ISSUES.md"])
    version = tomllib.loads(files["pyproject.toml"])["project"]["version"]
    package = json.loads(files["web/package.json"])
    lock = json.loads(files["web/package-lock.json"])
    if any(
        v != version
        for v in (
            developer[0][0],
            package["version"],
            lock["version"],
            lock["packages"][""]["version"],
        )
    ):
        raise ValueError("Package versions must match latest released version")
    for pattern in (
        r"^- 現在のバージョン: `([^`]+)`$",
        r"^リリース時は `([^`]+)` が次の場所で一致していることを確認します。$",
    ):
        if re.findall(pattern, files["README.md"], re.M) != [version]:
            raise ValueError("README version must match latest release")
    return version


SOURCE_PATHS = (
    "CHANGELOG.md",
    "RELEASE_NOTES.md",
    "KNOWN_ISSUES.md",
    "pyproject.toml",
    "web/package.json",
    "web/package-lock.json",
    "README.md",
)


def read_sources(root: Path) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in SOURCE_PATHS}
