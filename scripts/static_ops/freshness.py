"""Deployment-local scheduling hint; integrity remains an Actions responsibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from .operations import VerificationError, monitor_catalog
except ImportError:
    from operations import VerificationError, monitor_catalog

LIMIT = 2048
NAME = "weather/msm/freshness.json"


def inventory(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def mismatch(target: str, expected: dict, observed: dict) -> VerificationError:
    def safe(record):
        result = identity(record)
        if type(record.get("bytes")) is int:
            result["bytes"] = record["bytes"]
        digest = record.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            result["sha256"] = digest
        return result

    return VerificationError(json.dumps(
        {"target": target, "expected": safe(expected), "observed": safe(observed)}
    ))


def identity(value: dict) -> dict:
    """Only log validated identifiers, never arbitrary remote metadata/configuration."""
    result = {}
    for key, pattern in (("commit", r"[a-f0-9]{40}"), ("app_commit", r"[a-f0-9]{40}"),
                         ("run", r"[0-9]{14}")):
        if key in value:
            item = value[key]
            result[key] = (
                item if isinstance(item, str) and re.fullmatch(pattern, item) else "invalid"
            )
    return result


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
    expected = document(catalog, release["commit"])
    record = release["files"].get(NAME)
    if len(raw) > LIMIT or record != inventory(raw):
        raise mismatch(NAME, {**(record or {}), **identity(expected)}, inventory(raw))
    observed = json.loads(raw)
    if observed != expected:
        raise mismatch(
            NAME, {**record, **identity(expected)},
            {**inventory(raw), **(identity(observed) if isinstance(observed, dict) else {})},
        )


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


def canonical(origin: str, commit: str, fetcher, *, candidate: dict | None = None) -> dict | None:
    """Check canonical inventory and payloads, allowing a legacy deployment without a hint."""
    base = origin.rstrip("/")
    release = json.loads(fetcher(base + "/release.json", 8 * 1024**2))
    if candidate is not None and release != candidate:
        # Stable semantic fingerprints diagnose any inventory/configuration difference
        # without exposing the configuration or arbitrary response contents.
        raise mismatch("release.json",
                       {**inventory(json.dumps(candidate, sort_keys=True).encode()),
                        **identity(candidate)},
                       {**inventory(json.dumps(release, sort_keys=True).encode()),
                        **(identity(release) if isinstance(release, dict) else {})})
    if release["commit"] != commit or release["dirty"] is not False:
        raise ValueError("Canonical application identity mismatch")
    raw = fetcher(base + "/weather/msm/catalog.json", 8 * 1024**2)
    received = datetime.now(UTC)
    weather = None
    if candidate is not None:
        weather = monitor_catalog(
            raw, received, received=received,
            expected=candidate["files"]["weather/msm/catalog.json"],
        )
    catalog = json.loads(raw)
    if release["files"].get("weather/msm/catalog.json") != inventory(raw):
        raise mismatch("weather/msm/catalog.json",
                       release["files"].get("weather/msm/catalog.json", {}), inventory(raw))
    hint = document(catalog, commit)
    if NAME in release["files"]:
        check(fetcher(base + "/" + NAME, LIMIT), catalog, release)
    for asset in catalog["assets"]:
        if (not re.fullmatch(r"[a-f0-9]{64}", asset["sha256"])
                or asset["file"] != asset["sha256"] + ".npz"
                or type(asset["bytes"]) is not int or not 0 < asset["bytes"] <= 25 * 1024**2):
            raise ValueError("Invalid canonical asset")
        record = {"bytes": asset["bytes"], "sha256": asset["sha256"]}
        if release["files"].get("weather/msm/" + asset["file"]) != record:
            raise mismatch("weather/msm/" + asset["file"],
                           release["files"].get("weather/msm/" + asset["file"], {}), record)
        payload = fetcher(base + "/weather/msm/" + asset["file"], 25 * 1024**2)
        if inventory(payload) != record:
            raise mismatch("weather/msm/" + asset["file"],
                           {**record, "run": asset["run"], "commit": commit}, inventory(payload))
    if weather is not None:
        return {"commit": commit, "run": hint["run"], **weather}
    return None


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
