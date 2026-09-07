#!/usr/bin/env python3
"""Validate release sources, optionally checking clear PR fragment omissions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from release_content import fragment_paths, parse_fragment, read_sources, validate_sources


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args], text=True
    )


def runtime_path(path: str) -> bool:
    return (
        path.startswith(("src/", "web/src/", "web/scripts/", "data/", ".github/workflows/"))
        or path
        in {
            "Dockerfile",
            ".dockerignore",
            "compose.yaml",
            "docker-compose.yml",
            "pyproject.toml",
            "web/package.json",
            "web/package-lock.json",
        }
        or bool(re.fullmatch(r"web/(?:vite|tsconfig|postcss|tailwind).*", path))
    )


def meaningful_diff(diff: str, path: str = "") -> bool:
    """Exempt clear comments and JSX copy; leave complex expressions to release review."""
    comments = ("//", "/*", "*/", "<!--", "-->")
    if path.endswith((".py", ".yml", ".yaml", ".toml", ".sh")) or path in (
        "Dockerfile",
        ".dockerignore",
    ):
        comments += ("#",)
    before: list[str] = []
    after: list[str] = []
    changed = False
    for line in diff.splitlines():
        if line[:1] not in ("+", "-", " ") or line.startswith(("+++", "---")):
            continue
        content = line[1:].strip()
        if not content or content.startswith(comments):
            continue
        changed |= line[0] in ("+", "-")
        if line[0] != "+":
            before.append(content)
        if line[0] != "-":
            after.append(content)
    if path.endswith((".tsx", ".jsx")) and before and after:
        # Only erase text inside JSX tags, never arbitrary strings such as URLs or schema keys.
        literal = r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'"
        conditional = rf"(?:!?[A-Za-z_$][\w.$]*\s*\?\s*(?:{literal})\s*:\s*)?"
        attribute = (
            rf"\b(aria-label|title|placeholder)=({literal}|\{{\s*{conditional}(?:{literal})\s*\}})"
        )

        def without_copy(source: str) -> str:
            def attribute_copy(match: re.Match[str]) -> str:
                value = match[2]
                if value.startswith("{"):
                    value = re.sub(literal, '"COPY"', value)
                else:
                    value = '"COPY"'
                return f"{match[1]}={value}"

            source = re.sub(
                r"<[A-Za-z][^<>]*>",
                lambda tag: re.sub(attribute, attribute_copy, tag[0]),
                source,
            )
            return re.sub(r"(<[A-Za-z][^<>]*>)[^<>{}]+(?=</)", r"\1COPY", source)

        if without_copy("\n".join(before)) == without_copy("\n".join(after)):
            return False
    return changed


def validate(root: Path, base: str | None = None) -> None:
    current = validate_sources(read_sources(root))
    paths = fragment_paths(root)
    for path in paths:
        try:
            parse_fragment(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError(f"{path.name}: {error}") from error
    if not base:
        return
    # Use the PR merge base; main may have moved after the branch was created.
    merge_base = git(root, "merge-base", base, "HEAD").strip()
    previous = json.loads(git(root, "show", f"{merge_base}:web/package.json"))["version"]
    if current != previous:
        if paths:
            raise ValueError("A finalized release must consume every fragment")
        return
    changed = git(root, "diff", "--name-only", merge_base, "HEAD").splitlines()
    required = [
        path
        for path in changed
        if runtime_path(path)
        and meaningful_diff(git(root, "diff", "--unified=3", merge_base, "HEAD", "--", path), path)
    ]
    added = git(
        root, "diff", "--name-only", "--diff-filter=A", merge_base, "HEAD", "--", "changes/"
    ).splitlines()
    if required and not any(path != "changes/README.md" and path.endswith(".md") for path in added):
        raise ValueError(
            "Runtime/build changes require a new change fragment: " + ", ".join(required)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PR base commit; omit for push validation")
    args = parser.parse_args()
    try:
        validate(Path(__file__).resolve().parents[1], args.base)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print("Release sources are valid.")


if __name__ == "__main__":
    main()
