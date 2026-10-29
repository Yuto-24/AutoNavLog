"""Exercise the workflow's Docker-to-nonroot handoff without network or publication."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.static_ops import freshness, publication_gate  # noqa: E402


def record(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def seed(dist, existing_hint):
    """Create realistic 0755/0644 build outputs as Docker's default root user."""
    assert os.geteuid() == 0
    os.umask(0o022)
    generated = datetime.now(UTC).replace(microsecond=0)
    run = generated.replace(hour=generated.hour // 3 * 3, minute=0, second=0)
    payload = b"forecast fixture; real NPZ coverage is tested by validate_feed"
    digest = hashlib.sha256(payload).hexdigest()
    files = {"index.html": b"app", "404.html": b"missing", "_headers": b"headers",
             "local/core.whl": b"wheel", "local/data.zip": b"data",
             f"weather/msm/{digest}.npz": payload}
    files["local/manifest.json"] = json.dumps({
        "version": "1.0.0", "pyodideVersion": "0.27.7", "wheels": ["core.whl"],
        "data": "data.zip", "sha256": {"core.whl": record(b"wheel")["sha256"],
                                          "data.zip": record(b"data")["sha256"]},
    }).encode()
    files["weather/msm/catalog.json"] = json.dumps({
        "schema_version": 1, "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(hours=6)).isoformat(),
        "assets": [{"file": digest + ".npz", "sha256": digest, "bytes": len(payload),
                    "run": run.strftime("%Y%m%d%H%M%S")}],
    }).encode()
    for name, raw in files.items():
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (dist / "release.json").write_text(json.dumps({
        "commit": "a" * 40, "dirty": False, "version": "1.0.0", "pyodideVersion": "0.27.7",
        "files": {name: record(raw) for name, raw in files.items()},
    }))
    if existing_hint:
        freshness.write(dist)


def snapshot(dist):
    return {p.relative_to(dist).as_posix():
            (p.stat().st_uid, p.stat().st_gid, stat.S_IMODE(p.stat().st_mode),
             record(p.read_bytes()) if p.is_file() else None)
            for p in dist.rglob("*")}


def check_handoff(image):
    assert os.geteuid() != 0, "Run on a nonroot Actions/host user, never skip this contract"
    workflow = (ROOT / ".github/workflows/static-production.yml").read_text()
    start = workflow.index("              # Only these outputs need host-side writes;")
    feed_handoff = workflow[start:workflow.index("\n            '\n", start)]
    app_script = (ROOT / "scripts/static_ops/build_app_release.sh").read_text()
    app_handoff = app_script[app_script.index("# Only the hint directory/inventory"):]
    handoffs = {"feed": feed_handoff, "app": app_handoff}
    # Execute the actual workflow tail, not an independent copy of its chown logic.
    for kind, existing_hint in product(handoffs, (False, True)):
        handoff = handoffs[kind]
        with tempfile.TemporaryDirectory(prefix="static-artifact-handoff-") as temporary:
            workspace = Path(temporary)
            app = workspace / "app"
            app.mkdir()
            dist = app / "web/dist-static"
            mounts = ["--mount", f"type=bind,source={app},target=/app",
                      "--mount", f"type=bind,source={ROOT},target=/tooling,readonly"]
            subprocess.run(["docker", "run", "--rm", *mounts, image, "python",
                            "/tooling/tests/integration/check_static_artifact_handoff.py",
                            "--seed", str(int(existing_hint))], check=True)
            try:
                before = snapshot(dist)
                assert all(uid == gid == 0 for uid, gid, _, _ in before.values())
                assert before["weather/msm"][:3] == (0, 0, 0o755)
                assert before["release.json"][:3] == (0, 0, 0o644)
                try:
                    freshness.write(dist)
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Root-owned artifact unexpectedly writable by nonroot")
                subprocess.run(["docker", "run", "--rm", *mounts,
                                "--env", f"ARTIFACT_UID={os.getuid()}",
                                "--env", f"ARTIFACT_GID={os.getgid()}",
                                image, "sh", "-eu", "-c", handoff], check=True)
                handed = snapshot(dist)
                changed = {"release.json", "weather/msm"}
                if existing_hint:
                    changed.add(freshness.NAME)
                for name, original in before.items():
                    current = handed[name]
                    assert current[2:] == original[2:], name  # modes and bytes unchanged
                    assert current[:2] == ((os.getuid(), os.getgid()) if name in changed
                                           else original[:2]), name
                freshness.write(dist)
                publication_gate.check_static(dist)
                release = json.loads((dist / "release.json").read_bytes())
                actual = {p.relative_to(dist).as_posix(): record(p.read_bytes())
                          for p in dist.rglob("*") if p.is_file() and p.name != "release.json"}
                assert release["files"] == actual
                assert release["commit"] == "a" * 40 and release["dirty"] is False
                after = snapshot(dist)
                for name in before.keys() - {"release.json", freshness.NAME}:
                    assert after[name] == handed[name], name
                freshness.write(dist)  # repeat writes must not renew forecast lifetime
                assert snapshot(dist) == after
                print(json.dumps({"status": "OK", "runner_uid": os.getuid(),
                                  "publication": kind,
                                  "runner_gid": os.getgid(), "build_uid_gid": "0:0",
                                  "build_directory_mode": "0755", "build_file_mode": "0644",
                                  "existing_hint": existing_hint, "inventory": "exact"}))
            finally:
                # Only this test's disposable bind mount; never a runtime or data volume.
                subprocess.run(["docker", "run", "--rm", *mounts, image, "python", "-c",
                                "import shutil; shutil.rmtree('/app/web')"], check=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--seed":
        seed(Path("/app/web/dist-static"), bool(int(sys.argv[2])))
    else:
        check_handoff(sys.argv[1] if len(sys.argv) > 1 else "autonavlog-static-build")
