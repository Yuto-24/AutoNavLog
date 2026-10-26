"""Deployment-local scheduling hint; integrity remains an Actions responsibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

LIMIT = 2048
NAME = "weather/msm/freshness.json"


def document(catalog: dict, commit: str) -> dict:
    """Derive a hint from actual catalog assets and the approved application identity."""
    generated = datetime.fromisoformat(catalog["generated_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(catalog["expires_at"].replace("Z", "+00:00"))
    if (catalog["schema_version"] != 1 or not catalog["assets"] or generated.tzinfo is None
            or expires - generated != timedelta(hours=6)
            or not re.fullmatch(r"[a-f0-9]{40}", commit)):
        raise ValueError("Invalid freshness source")
    for asset in catalog["assets"]:
        run = datetime.strptime(asset["run"], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if run > generated or run.hour % 3 or run.minute or run.second:
            raise ValueError("Invalid freshness Run")
    return {"schema_version": 1, "run": max(a["run"] for a in catalog["assets"]),
            "generated_at": catalog["generated_at"], "expires_at": catalog["expires_at"],
            "app_commit": commit}


def check(raw: bytes, catalog: dict, release: dict) -> None:
    """Require exact catalog/identity agreement; a hint alone proves nothing."""
    if len(raw) > LIMIT or json.loads(raw) != document(catalog, release["commit"]):
        raise ValueError("Freshness/catalog mismatch")
    if release["files"].get(NAME) != {
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()
    }:
        raise ValueError("Freshness/release mismatch")


def write(dist: Path) -> None:
    """Add the hint to a validated build and its inventory without changing the app checkout."""
    release_file = dist / "release.json"
    release = json.loads(release_file.read_bytes())
    catalog = json.loads((dist / "weather/msm/catalog.json").read_bytes())
    raw = (json.dumps(document(catalog, release["commit"]), separators=(",", ":")) + "\n").encode()
    if len(raw) > LIMIT:
        raise ValueError("Freshness exceeds budget")
    (dist / NAME).write_bytes(raw)
    release["files"][NAME] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    release_file.write_text(json.dumps(release, indent=2) + "\n")
    check(raw, catalog, release)


def canonical(origin: str, commit: str, fetcher) -> None:
    """Check canonical inventory and payloads, allowing a legacy deployment without a hint."""
    base = origin.rstrip("/")
    release = json.loads(fetcher(base + "/release.json", 8 * 1024**2))
    if release["commit"] != commit or release["dirty"] is not False:
        raise ValueError("Canonical application identity mismatch")
    raw = fetcher(base + "/weather/msm/catalog.json", 8 * 1024**2)
    catalog = json.loads(raw)
    if release["files"].get("weather/msm/catalog.json") != {
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()
    }:
        raise ValueError("Catalog/release mismatch")
    document(catalog, commit)
    if NAME in release["files"]:
        check(fetcher(base + "/" + NAME, LIMIT), catalog, release)
    for asset in catalog["assets"]:
        if (not re.fullmatch(r"[a-f0-9]{64}", asset["sha256"])
                or asset["file"] != asset["sha256"] + ".npz"
                or type(asset["bytes"]) is not int or not 0 < asset["bytes"] <= 25 * 1024**2):
            raise ValueError("Invalid canonical asset")
        record = {"bytes": asset["bytes"], "sha256": asset["sha256"]}
        if release["files"].get("weather/msm/" + asset["file"]) != record:
            raise ValueError("Asset/release mismatch")
        payload = fetcher(base + "/weather/msm/" + asset["file"], 25 * 1024**2)
        if len(payload) != asset["bytes"] or hashlib.sha256(payload).hexdigest() != asset["sha256"]:
            raise ValueError("Canonical payload mismatch")


def main() -> None:
    """Write the deployment hint or independently verify canonical payload integrity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--origin")
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.dist:
        write(args.dist)
    elif args.origin and args.commit:
        try:
            from .operations import fetch
        except ImportError:
            from operations import fetch
        canonical(args.origin, args.commit, fetch)
    else:
        parser.error("Use --dist or --origin with --commit")


if __name__ == "__main__":
    main()
