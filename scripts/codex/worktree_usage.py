#!/usr/bin/env python3
"""Read-only Docker worktree inventory; never infer permission to delete from it."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

VOLUME = re.compile(r"autonavlog-wt-[0-9a-f]{8}_autonavlog-data")
# No symlink traversal, count allocated blocks (like du) once per inode.
MEASURE = '''
import json, os
from pathlib import Path
root = Path('/data')
seen = set()
def usage(path):
    st = path.lstat()
    key = (st.st_dev, st.st_ino)
    if key in seen:
        return 0
    seen.add(key)
    size = st.st_blocks * 512
    if path.is_dir() and not path.is_symlink():
        size += sum(usage(child) for child in path.iterdir())
    return size
print(json.dumps({p.name: usage(p) for p in sorted(root.iterdir())}))
'''


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def inspect(kind: str, names: list[str]) -> list[dict]:
    return json.loads(command("docker", kind, "inspect", *names)) if names else []


def summarize(rows: list[dict]) -> dict:
    measured = [row for row in rows if row.get("usage_bytes") is not None]
    return {
        "volumes": len(rows),
        "measured_volumes": len(measured),
        "known_total_bytes": sum(sum(row["usage_bytes"].values()) for row in measured),
        "known_msm_cache_bytes": sum(row["usage_bytes"].get("msm-cache", 0) for row in measured),
        "complete": len(measured) == len(rows),
    }


def inventory(image: str) -> dict:
    names = [n for n in command("docker", "volume", "ls", "-q").splitlines()
             if VOLUME.fullmatch(n)]
    containers = inspect("container", command("docker", "ps", "-aq").splitlines())
    rows = []
    for volume in inspect("volume", names):
        name = volume["Name"]
        owners = []
        for container in containers:
            if not any(m.get("Name") == name for m in container["Mounts"]):
                continue
            labels = container["Config"].get("Labels") or {}
            cwd = labels.get("com.docker.compose.project.working_dir")
            git_status = None
            if cwd and Path(cwd).is_dir():
                try:
                    git_status = command("git", "-C", cwd, "status", "--porcelain")
                except subprocess.CalledProcessError:
                    pass
            owners.append({
                "container": container["Name"].lstrip("/"),
                "state": container["State"]["Status"],
                "restart": container["HostConfig"]["RestartPolicy"]["Name"],
                "project": labels.get("com.docker.compose.project"),
                "worktree": cwd,
                "git_status": git_status,
            })
        row = {"volume": name, "labels": volume.get("Labels"), "owners": owners,
               "usage_bytes": None}
        try:
            row["usage_bytes"] = json.loads(command(
                "docker", "run", "--rm", "--pull", "never", "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "0:0", "--mount", f"type=volume,src={name},dst=/data,readonly",
                # Mask the image's VOLUME so diagnostics cannot create anonymous volumes.
                "--tmpfs", "/var/lib/autonavlog:rw,noexec,nosuid,size=1m",
                "--entrypoint", "python", image, "-c", MEASURE,
            ))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            row["error"] = str(error)
        rows.append(row)
    return {"summary": summarize(rows), "worktrees": rows,
            "note": "Live snapshot; unknown usage is not zero. Check active tasks before cleanup."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="autonavlog:local")
    args = parser.parse_args()
    report = inventory(args.image)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["summary"]["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
