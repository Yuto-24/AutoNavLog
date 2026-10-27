#!/usr/bin/env python3
"""Publish immutable, branch-specific GitHub releases (never deploy the app)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from release_content import parse_version_source, validate_sources, version_tuple


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def api(path: str) -> dict | None:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if result.returncode:
        # Only an explicit missing resource is absence; auth/network/server errors fail closed.
        if "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def release_line(ref: str, version: str) -> tuple[str, str]:
    lines = {
        "refs/heads/main": (2, "NavMate", "true"),
        "refs/heads/release/1.x": (1, "AutoNavLog", "false"),
    }
    if ref not in lines:
        raise ValueError(f"unsupported release ref: {ref}")
    major, product, latest = lines[ref]
    if version_tuple(version)[0] != major:
        raise ValueError(f"{ref} requires major version {major}, got {version}")
    return product, latest


def release_notes(source: str, version: str) -> str:
    lines = source.splitlines()
    start = lines.index(f"## {version}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    notes = "\n".join(lines[start:end]).strip()
    if not notes:
        raise ValueError(f"CHANGELOG has no notes for {version}")
    return notes + "\n"


def tag_commit(repository: str, tag: str) -> str | None:
    ref = api(f"repos/{repository}/git/ref/tags/{tag}")
    if ref is None:
        return None
    obj = ref["object"]
    # Peel annotated tags, including tags of tags; never move or replace any tag.
    for _ in range(16):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        annotated = api(f"repos/{repository}/git/tags/{obj['sha']}")
        if annotated is None:
            break
        obj = annotated["object"]
    raise ValueError(f"{tag} does not resolve to a commit")


def publish(root: Path, ref: str, sha: str, repository: str) -> None:
    version = validate_sources(root)
    product, latest = release_line(ref, version)
    if run("git", "rev-parse", "HEAD") != sha:
        raise ValueError("checkout HEAD does not match the triggering commit")
    tag = f"v{version}"
    notes = release_notes((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    commit = tag_commit(repository, tag)
    release = api(f"repos/{repository}/releases/tags/{tag}")
    if release is not None and commit is None:
        raise ValueError(f"existing release {tag} has no tag commit")
    if commit is not None:
        # Later maintenance pushes with unchanged VERSION legitimately reuse an ancestor tag.
        # A foreign/diverged tag or changed release sources must not silently skip publication.
        run("git", "merge-base", "--is-ancestor", commit, sha)
        tagged_version = parse_version_source(run("git", "show", f"{commit}:VERSION"))
        tagged_notes = release_notes(run("git", "show", f"{commit}:CHANGELOG.md"), version)
        if tagged_version != version or tagged_notes != notes:
            raise ValueError(f"{tag} points to different release sources")
    if release is not None:
        if release.get("draft") or release.get("prerelease"):
            raise ValueError(f"{tag} is not an existing published stable release")
        print(f"{tag} already published at verified commit {commit}; unchanged.")
        return
    with tempfile.TemporaryDirectory() as directory:
        notes_file = Path(directory) / "release-notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        args = [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--target",
            commit or sha,
            "--title",
            f"{product} {version}",
            "--notes-file",
            str(notes_file),
            f"--latest={latest}",
        ]
        if commit is not None:
            args.append("--verify-tag")
        run(*args)


if __name__ == "__main__":
    publish(
        Path.cwd(),
        os.environ["GITHUB_REF"],
        os.environ["GITHUB_SHA"],
        os.environ["GITHUB_REPOSITORY"],
    )
