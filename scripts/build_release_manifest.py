#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    root = args.release_directory
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "release-manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": 1,
        "autonavlog_version": args.version,
        "msm_package_version": "0.2.1",
        "msm_commit": "52adad68fd39d377c77774265229ea3b320c2c92",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
