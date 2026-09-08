#!/usr/bin/env python3
"""Plan and finalize one release, consuming every change fragment; never calls git."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from release_content import (
    SECTIONS,
    fragment_paths,
    parse_fragment,
    read_sources,
    validate_sources,
    version_tuple,
)

FALLBACK = "内部処理を改善しました。AutoNavLogの使い方に変更はありません。"


def prepend_release(source: str, version: str, stamp: str, sections: dict[str, list[str]]) -> str:
    header = re.search(r"^## ", source, re.MULTILINE)
    if header is None:
        raise ValueError("Release history has no version header")
    insertion = header.start()
    entry = f"## {version} - {stamp}\n\n"
    for title, bullets in sections.items():
        if bullets:
            entry += f"### {title}\n\n" + "".join(f"- {text}\n" for text in bullets) + "\n"
    return source[:insertion] + entry + source[insertion:]


def plan_release(root: Path, target: str) -> tuple[dict[str, str], list[Path]]:
    target_tuple = version_tuple(target)
    sources = read_sources(root)
    current = validate_sources(sources)
    if target_tuple <= version_tuple(current):
        raise ValueError("Target version must be newer than the current release")
    paths = fragment_paths(root)
    if not paths:
        raise ValueError("No change fragments to release")
    fragments = [parse_fragment(path.read_text(encoding="utf-8")) for path in paths]
    developer = {section: [] for section in SECTIONS}
    user = {section: [] for section in SECTIONS}
    for fragment in fragments:
        developer[fragment["section"]].extend(fragment["developer"])
        user[fragment["section"]].extend(fragment["user"])
    if not any(user.values()):
        user["改善"].append(FALLBACK)
    developer["配布"] = [f"Python・Web のバージョンを `{target}` に更新しました。"]
    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    outputs = dict(sources)
    outputs["CHANGELOG.md"] = prepend_release(sources["CHANGELOG.md"], target, stamp, developer)
    outputs["RELEASE_NOTES.md"] = prepend_release(sources["RELEASE_NOTES.md"], target, stamp, user)
    outputs["pyproject.toml"], count = re.subn(
        rf'(?m)^version = "{re.escape(current)}"$',
        f'version = "{target}"',
        sources["pyproject.toml"],
    )
    if count != 1:
        raise ValueError("Expected exactly one project version assignment")
    for name in ("web/package.json", "web/package-lock.json"):
        document = json.loads(sources[name])
        document["version"] = target
        if name.endswith("package-lock.json"):
            document["packages"][""]["version"] = target
        outputs[name] = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    outputs["README.md"] = (
        sources["README.md"]
        .replace(f"- 現在のバージョン: `{current}`", f"- 現在のバージョン: `{target}`")
        .replace(f"リリース時は `{current}` が", f"リリース時は `{target}` が")
    )
    validate_sources(outputs)
    return {name: text for name, text in outputs.items() if text != sources[name]}, paths


def prepare_release(root: Path, target: str, *, check: bool = False) -> None:
    outputs, paths = plan_release(root, target)
    print(f"Release {target}: {len(paths)} fragments; update {', '.join(outputs)}")
    if check:
        print("Check complete; no files changed.")
        return
    # Validation above completes before the first write. Restore original bytes on I/O failure,
    # including fragments already consumed, so ordinary failures do not leave a partial release.
    originals = {root / name: (root / name).read_bytes() for name in outputs}
    originals.update({path: path.read_bytes() for path in paths})
    try:
        for name, text in outputs.items():
            (root / name).write_text(text, encoding="utf-8")
        for path in paths:
            path.unlink()
        if fragment_paths(root):
            raise ValueError("Fragments remain after release")
    except (OSError, ValueError):
        for path, content in originals.items():
            path.write_bytes(content)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare_release(Path(__file__).resolve().parents[1], args.version, check=args.check)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Release preparation failed: {error}\n")


if __name__ == "__main__":
    main()
