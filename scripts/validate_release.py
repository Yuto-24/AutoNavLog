#!/usr/bin/env python3
"""Validate the explicit Release contract of a pull request."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path

from release_content import parse_version_source, validate_sources, version_tuple


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args], text=True
    )


def base_version(root: Path, commit: str) -> str:
    if git(root, "ls-tree", "--name-only", commit, "--", "VERSION").strip():
        value = parse_version_source(git(root, "show", f"{commit}:VERSION"))
    else:
        # Only the transition PR has a base without VERSION. An invalid existing
        # VERSION must fail rather than silently use an obsolete package version.
        content = git(root, "show", f"{commit}:pyproject.toml")
        try:
            value = tomllib.loads(content)["project"]["version"]
        except (KeyError, tomllib.TOMLDecodeError) as error:
            raise ValueError("base has no VERSION or legacy package version") from error
    version_tuple(value)
    return value


def parse_contract(body: str) -> str:
    lines = re.sub(r"<!--[\s\S]*?-->", "", body).splitlines()
    markers = [
        line.strip()
        for line in lines
        if re.fullmatch(r"Release: (?:required|not-required)", line.strip())
    ]
    if len(markers) != 1:
        raise ValueError(
            "PR body requires exactly one Release: required or Release: not-required line"
        )
    kind = markers[0].removeprefix("Release: ")
    if kind == "not-required" and not any(
        re.fullmatch(r"Reason: \S.*", line.strip()) for line in lines
    ):
        raise ValueError("Release: not-required requires a Reason: line")
    return kind


def validate(root: Path, base: str | None = None, pr_body: str | None = None) -> None:
    current = validate_sources(root)
    if not base:
        return
    if pr_body is None:
        raise ValueError("PR validation requires the PR body")
    contract = parse_contract(pr_body)
    merge_base = git(root, "merge-base", base, "HEAD").strip()
    previous = base_version(root, base)
    changed = git(root, "diff", "--name-only", merge_base, "HEAD").splitlines()
    if contract == "required":
        if version_tuple(current) <= version_tuple(previous):
            raise ValueError("Release: required needs VERSION newer than base")
        if "VERSION" not in changed or "CHANGELOG.md" not in changed:
            raise ValueError("Release: required must change VERSION and CHANGELOG.md")
    else:
        if current != previous or "VERSION" in changed:
            raise ValueError("Release: not-required must not change VERSION")
        if "CHANGELOG.md" in changed:
            raise ValueError(
                "Release: not-required must not change CHANGELOG.md; "
                "record pending release notes in the PR body"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--pr-body-file")
    args = parser.parse_args()
    body = Path(args.pr_body_file).read_text(encoding="utf-8") if args.pr_body_file else None
    try:
        validate(Path(__file__).resolve().parents[1], args.base, body)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print("Release sources are valid.")


if __name__ == "__main__":
    main()
