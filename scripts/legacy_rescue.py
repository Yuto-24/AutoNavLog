#!/usr/bin/env python3
"""Offline Legacy rescue snapshots. No network, runtime control or production writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

FORMAT = "autonavlog-legacy-rescue-v1"
KEY = re.compile(r"[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
LINK_COLUMNS = "owner,account,subject,state,run,expires,plan,completed,error"
RECEIPT_COLUMNS = "owner,id,previous,intended"
SCHEMA = (
    "CREATE TABLE links (owner TEXT PRIMARY KEY, account TEXT UNIQUE NOT NULL, "
    "subject TEXT UNIQUE NOT NULL, state TEXT NOT NULL, run TEXT, expires REAL, "
    "plan TEXT NOT NULL DEFAULT '[]', completed INTEGER NOT NULL DEFAULT 0, error TEXT)",
    "CREATE TABLE receipts (owner TEXT NOT NULL, id TEXT NOT NULL, previous TEXT, "
    "intended TEXT NOT NULL, PRIMARY KEY(owner,id))",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, "Dates must include a timezone")
    return result.astimezone(UTC)


def owner_key(identity: str) -> str:
    require(isinstance(identity, str) and bool(identity), "Missing owner identity")
    return hashlib.sha256(b"AutoNavLog OwnerProjectState v1\0" + identity.encode()).hexdigest()


def safe_root(path: Path) -> Path:
    path = path.absolute()
    require(all(not p.is_symlink() for p in (path, *path.parents)), "Symlink path refused")
    return path


def files(root: Path) -> dict[str, Path]:
    """Reject links/devices, including dangling links; never follow them."""
    safe_root(root)
    require(root.is_dir(), "Directory required")
    result = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), "Non-regular storage entry refused")
        if stat.S_ISREG(mode):
            require(path.stat().st_nlink == 1, "Hard-linked storage entry refused")
            result[path.relative_to(root).as_posix()] = path
    return result


def fingerprint(paths: dict[str, Path]) -> dict:
    return {name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "bytes": path.stat().st_size} for name, path in paths.items()}


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def read_json(path: Path) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "Duplicate JSON key")
            value[key] = item
        return value

    def number(raw):
        result = float(raw)
        require(math.isfinite(result), "Nonfinite JSON number")
        return result

    value = json.loads(path.read_bytes(), object_pairs_hook=pairs,
                       parse_float=number, parse_constant=number)
    require(isinstance(value, dict), "JSON object required")
    return value


def source_files(root: Path) -> dict[str, Path]:
    safe_root(root)
    require(root.is_dir(), "Storage root missing")
    result = {}
    # Weather caches, sessions, credentials and runtime configuration are not rescue data.
    for name in ("projects", "owners"):
        path = root / name
        require(not path.is_symlink(), "Symlink storage directory refused")
        if path.exists():
            result.update({f"{name}/{n}": p for n, p in files(path).items()})
    for name in ("account-links.sqlite3", "account-links.sqlite3-wal",
                 "account-links.sqlite3-shm", "account-links.sqlite3-journal"):
        path = root / name
        require(not path.is_symlink(), "Symlink registry refused")
        if path.exists():
            require(path.is_file() and path.stat().st_nlink == 1, "Unsafe registry file")
            result[name] = path
    require("account-links.sqlite3" in result, "Migration registry missing")
    return result


def database(path: Path):
    return closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))


def registry_check(path: Path) -> None:
    with database(path) as db:
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "Registry integrity check failed")
        require({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                == {"links", "receipts"}, "Unexpected registry tables")
        require(not db.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','view')")
                .fetchall(), "Unexpected registry executable schema")
        for table, columns in (("links", LINK_COLUMNS), ("receipts", RECEIPT_COLUMNS)):
            require([r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                    == columns.split(","), "Unexpected registry columns")
        require(not db.execute("SELECT 1 FROM links WHERE state='MIGRATING'").fetchone(),
                "Active/interrupted migration must be resolved before final snapshot")
        require(not db.execute("SELECT 1 FROM links WHERE state NOT IN "
                               "('LINKED','NAVMATE_ACTIVE') OR state IS NULL").fetchone(),
                "Unknown migration state")
        require(not db.execute("SELECT 1 FROM receipts LEFT JOIN links USING(owner) "
                               "WHERE links.owner IS NULL").fetchone(), "Orphan migration receipt")


def new_destination(destination: Path, source: Path) -> Path:
    destination = safe_root(destination)
    source = safe_root(source)
    require(not destination.is_relative_to(source) and not source.is_relative_to(destination),
            "Source and destination must be separate trees")
    require(destination.parent.is_dir(), "Destination parent must already exist")
    destination.mkdir(mode=0o700)  # Refuse an existing output, including failed attempts.
    return destination


def snapshot(source: Path, destination: Path, *, ended_at: str, retain_until: str,
             revision: str, quiescence: str) -> dict:
    now = datetime.now(UTC)
    require(instant(ended_at) <= now < instant(retain_until), "Invalid migration/retention dates")
    require(bool(SHA.fullmatch(revision)), "Full source revision required")
    require(bool(quiescence.strip()), "Quiescence evidence reference required")
    source = safe_root(source)
    before = fingerprint(source_files(source))
    destination = new_destination(destination, source)
    target = destination / "data/account-links.sqlite3"
    # Even mode=ro SQLite may write shared-memory lock bookkeeping. Open only private
    # copies, never the source database. Include WAL so committed transactions survive.
    with tempfile.TemporaryDirectory(prefix="sqlite-", dir=destination) as temporary:
        scratch = Path(temporary)
        for name, path in source_files(source).items():
            write((scratch if name.startswith("account-links.sqlite3")
                   else destination / "data") / name, path.read_bytes())
        registry_check(scratch / "account-links.sqlite3")
        write(target, b"")
        with database(scratch / "account-links.sqlite3") as db:
            with closing(sqlite3.connect(target)) as out:
                db.backup(out)
                out.execute("PRAGMA journal_mode=DELETE")
    registry_check(target)
    require(fingerprint(source_files(source)) == before, "Source changed during snapshot")
    inventory = fingerprint(files(destination / "data"))
    require({n: v for n, v in inventory.items() if n != "account-links.sqlite3"}
            == {n: v for n, v in before.items() if not n.startswith("account-links.sqlite3")},
            "Copied storage differs from source")
    manifest = {"format": FORMAT, "kind": "full", "created_at": now.isoformat(),
                "migration_ended_at": ended_at, "retain_until": retain_until,
                "source_revision": revision, "quiescence_reference": quiescence,
                "files": inventory}
    # A failed attempt has no manifest and is never a verified snapshot.
    write(destination / "manifest.json", json_bytes(manifest))
    return verify(destination)


def verify(bundle: Path, *, expected_digest: str | None = None) -> dict:
    bundle = safe_root(bundle)
    entries = files(bundle)
    require("manifest.json" in entries, "Incomplete snapshot: manifest missing")
    if expected_digest is not None:
        require(bool(KEY.fullmatch(expected_digest))
                and hashlib.sha256(entries["manifest.json"].read_bytes()).hexdigest()
                == expected_digest, "Manifest differs from separately retained digest")
    manifest = read_json(entries["manifest.json"])
    require(manifest["format"] == FORMAT and manifest["kind"] in {"full", "owner"},
            "Unsupported snapshot")
    require(instant(manifest["migration_ended_at"]) <= instant(manifest["created_at"])
            < instant(manifest["retain_until"]), "Invalid snapshot dates")
    require(bool(SHA.fullmatch(manifest["source_revision"])), "Invalid source revision")
    require(bool(manifest["quiescence_reference"].strip()), "Missing quiescence evidence")
    require(set(entries) == {"manifest.json", *(f"data/{n}" for n in manifest["files"])},
            "Unexpected or missing bundle files")
    require(fingerprint(files(bundle / "data")) == manifest["files"], "Snapshot hash mismatch")
    registry_check(bundle / "data/account-links.sqlite3")
    return manifest


def project_owners(data: Path) -> dict[str, str]:
    """Never trust index.json: check every saved/draft/backup/calculation body."""
    result = {}
    for name, path in files(data).items():
        parts = Path(name).parts
        if parts[0] != "projects" or name in {"projects/index.json", "projects/index.json.bak"}:
            continue
        require(len(parts) == 3 and str(UUID(parts[1])) == parts[1], "Unknown Project path")
        payload = read_json(path)
        is_calculation = parts[2] in {"last-calculation.json", "last-calculation.invalid.json"}
        project = payload["project"] if is_calculation else payload
        require(project["id"] == parts[1], "Project path identity mismatch")
        key = owner_key(project["metadata"]["web_owner_id"])
        if is_calculation:
            require(payload["owner_key"] == key and payload["project_id"] == parts[1]
                    and payload["outcome"]["project_id"] == parts[1], "Calculation owner mismatch")
        require(result.setdefault(parts[1], key) == key, "Mixed ownership within Project")
    return result


def extract_owner(bundle: Path, destination: Path, key: str, *, expected_digest: str) -> dict:
    require(bool(KEY.fullmatch(key)), "Owner key must be a SHA-256 digest")
    manifest = verify(bundle, expected_digest=expected_digest)
    require(manifest["kind"] == "full", "Select owners only from a full rescue snapshot")
    require(datetime.now(UTC) < instant(manifest["retain_until"]), "Retention expired")
    data = safe_root(bundle) / "data"
    owners = project_owners(data)
    project_ids = {pid for pid, owner in owners.items() if owner == key}
    with database(data / "account-links.sqlite3") as db:
        links = db.execute(f"SELECT {LINK_COLUMNS} FROM links WHERE owner=?", (key,)).fetchall()
        receipts = db.execute(f"SELECT {RECEIPT_COLUMNS} FROM receipts WHERE owner=?",
                              (key,)).fetchall()
    require(bool(project_ids or links), "Owner not found")
    selected = {}
    for name, path in files(data).items():
        parts = Path(name).parts
        if (len(parts) == 3 and parts[0] == "projects" and parts[1] in project_ids):
            selected[name] = path
        elif len(parts) == 3 and parts[:2] == ("owners", key):
            state = read_json(path)
            ids = [state.get(field) for field in ("project_id", "last_opened_project_id",
                                                  "latest_draft_project_id")]
            ids += state.get("pending_cleanup_project_ids", [])
            require(all(pid is None or pid in project_ids for pid in ids),
                    "Owner state references missing/foreign Project; manual rescue required")
            selected[name] = path
    destination = new_destination(destination, safe_root(bundle))
    for name, path in selected.items():
        write(destination / "data" / name, path.read_bytes())
    target = destination / "data/account-links.sqlite3"
    write(target, b"")
    # Build a new database: deleting other owners from a copied SQLite file leaks free pages.
    with closing(sqlite3.connect(target)) as db:
        with db:
            for sql in SCHEMA:
                db.execute(sql)
            db.executemany(f"INSERT INTO links ({LINK_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", links)
            db.executemany(f"INSERT INTO receipts ({RECEIPT_COLUMNS}) VALUES (?,?,?,?)", receipts)
    require(verify(bundle) == manifest, "Source snapshot changed during extraction")
    require(fingerprint(selected) == {n: manifest["files"][n] for n in selected},
            "Selected source changed during extraction")
    require({n: v for n, v in fingerprint(files(destination / "data")).items()
             if n != "account-links.sqlite3"} == {n: manifest["files"][n] for n in selected},
            "Extracted files differ from snapshot")
    result = {**manifest, "kind": "owner", "owner_key": key,
              "parent_manifest_sha256": hashlib.sha256((bundle / "manifest.json")
                                                        .read_bytes()).hexdigest(),
              "files": fingerprint(files(destination / "data"))}
    write(destination / "manifest.json", json_bytes(result))
    return verify(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("snapshot")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--migration-ended-at", required=True)
    create.add_argument("--retain-until", required=True)
    create.add_argument("--source-revision", required=True)
    create.add_argument("--quiescence-reference", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--bundle", type=Path, required=True)
    check.add_argument("--expected-manifest-sha256", required=True)
    extract = sub.add_parser("extract-owner")
    extract.add_argument("--bundle", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.add_argument("--owner-key", required=True)
    extract.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(args.source, args.destination, ended_at=args.migration_ended_at,
                              retain_until=args.retain_until, revision=args.source_revision,
                              quiescence=args.quiescence_reference)
        elif args.command == "extract-owner":
            result = extract_owner(args.bundle, args.destination, args.owner_key,
                                   expected_digest=args.expected_manifest_sha256)
        else:
            result = verify(args.bundle, expected_digest=args.expected_manifest_sha256)
        # No identities, paths, Project payloads, registry rows or native errors in stdout.
        output = args.bundle if args.command == "verify" else args.destination
        print(json.dumps({"status": "VERIFIED", "kind": result["kind"],
                          "manifest_sha256": hashlib.sha256((output / "manifest.json")
                                                            .read_bytes()).hexdigest(),
                          "files": len(result["files"]), "retirement_authorized": False}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError, sqlite3.Error):
        print(json.dumps({"status": "BLOCKED", "retirement_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
