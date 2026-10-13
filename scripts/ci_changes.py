"""Skip expensive CI only for a proven documentation-only Git diff."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Change:
    path: str
    old_mode: str
    new_mode: str


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def commit_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value) or value == "0" * 40:
        raise ValueError("Missing or invalid commit SHA")
    return value


def changed_files(root: Path, event_name: str, event: dict) -> list[Change]:
    if event_name == "pull_request":
        base = commit_sha(event["pull_request"]["base"]["sha"])
        head = commit_sha(event["pull_request"]["head"]["sha"])
        base = git(root, "merge-base", base, head).decode().strip()
    elif event_name == "push":
        base = commit_sha(event["before"])
        head = commit_sha(event["after"])
    else:
        raise ValueError("Unsupported event")

    # Disable rename detection so moving code into docs still includes its deletion.
    # NUL delimiters preserve paths containing whitespace or newlines, without an API file limit.
    raw = git(root, "diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, head, "--")
    if not raw:
        return []
    fields = raw.split(b"\0")
    if fields.pop() != b"" or len(fields) % 2:
        raise ValueError("Invalid Git diff")
    changes = []
    for index in range(0, len(fields), 2):
        old_mode, new_mode, _, _, _ = fields[index].decode("ascii").split()
        if not old_mode.startswith(":"):
            raise ValueError("Invalid Git diff mode")
        changes.append(Change(os.fsdecode(fields[index + 1]), old_mode[1:], new_mode))
    return changes


def is_documentation(change: Change) -> bool:
    # Symlinks, submodules and executable-mode changes require the full checks.
    if {change.old_mode, change.new_mode} - {"000000", "100644"}:
        return False
    if change.path == "README.md":
        # Packaging needs the root README to exist as a regular file.
        return change.new_mode == "100644"
    return change.path == "AGENTS.md" or (
        change.path.startswith("docs/") and change.path.endswith(".md")
    )


def ci_scope(root: Path, event_name: str, event: dict) -> tuple[bool, str]:
    try:
        changes = changed_files(root, event_name, event)
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError):
        return True, "Changed files could not be determined; running full CI."
    if not changes:
        return True, "No changed files were found; running full CI."
    if all(is_documentation(change) for change in changes):
        return False, f"Documentation-only change ({len(changes)} files); source checks only."
    return True, f"Files outside the documentation allowlist changed ({len(changes)} files)."


def main(root: Path | None = None) -> None:
    root = root or Path(__file__).resolve().parents[1]
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    except (KeyError, ValueError, OSError):
        run_full, reason = True, "Event payload could not be read; running full CI."
    else:
        run_full, reason = ci_scope(root, os.environ.get("GITHUB_EVENT_NAME", ""), event)
    output = f"run_full={str(run_full).lower()}\n"
    print(reason)
    print(output, end="")
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_path).open("a", encoding="utf-8") as stream:
            stream.write(output)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(f"## CI scope\n\n{reason}\n")


if __name__ == "__main__":
    main()
