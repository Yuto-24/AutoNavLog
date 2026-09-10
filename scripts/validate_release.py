#!/usr/bin/env python3
"""Validate the explicit Release contract of a pull request."""

from __future__ import annotations

import argparse
import ast
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


def runtime_path(path: str) -> bool:
    if path.startswith("web/") and re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", path):
        return False
    return (
        path.startswith(("src/", "web/src/", "web/public/", "web/scripts/", "data/", "vendor/"))
        or path
        in {
            "VERSION",
            "CHANGELOG.md",
            "KNOWN_ISSUES.md",
            "Dockerfile",
            ".dockerignore",
            "compose.yaml",
            "docker-compose.yml",
            "pyproject.toml",
            "web/package.json",
            "web/package-lock.json",
            "web/index.html",
            "MANIFEST.in",
            "setup.py",
            "setup.cfg",
        }
        or bool(
            re.fullmatch(
                r"(?:compose[.\w-]*\.ya?ml|web/(?:vite|tsconfig|postcss|tailwind).*)", path
            )
        )
    )


class _WithoutDocstrings(ast.NodeTransformer):
    def visit_Module(self, node: ast.Module) -> ast.Module:
        return self._strip(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        return self._strip(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        return self._strip(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        return self._strip(node)

    def _strip(self, node):
        self.generic_visit(node)
        if ast.get_docstring(node, clean=False) is not None:
            node.body = node.body[1:]
        return node


def python_documentation_only(root: Path, base: str, path: str) -> bool:
    """Exempt unchanged Python syntax apart from comments and docstrings.

    File creation/deletion and syntax errors are never treated as documentation.
    Other languages remain subject to file-level classification, without guessing
    whether a comment-looking line might be inside a string literal.
    """
    if not path.endswith(".py"):
        return False
    try:
        before = git(root, "show", f"{base}:{path}")
        after = git(root, "show", f"HEAD:{path}")
        trees = [
            ast.dump(_WithoutDocstrings().visit(ast.parse(source))) for source in (before, after)
        ]
    except (subprocess.CalledProcessError, SyntaxError):
        return False
    return trees[0] == trees[1]


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
        runtime = [
            path
            for path in changed
            if runtime_path(path) and not python_documentation_only(root, merge_base, path)
        ]
        if runtime:
            raise ValueError(
                "Release: not-required cannot include production/runtime changes: "
                + ", ".join(runtime)
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
