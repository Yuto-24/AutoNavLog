#!/usr/bin/env python3
"""Read-only Legacy production preflight. Never print resolved config or secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_KEYS = (
    "AUTONAVLOG_CLOUDFLARE_TEAM_DOMAIN",
    "AUTONAVLOG_CLOUDFLARE_ACCESS_AUDIENCE",
    "AUTONAVLOG_FIREBASE_PROJECT_ID",
    "AUTONAVLOG_FIREBASE_API_KEY",
    "AUTONAVLOG_FIREBASE_AUTH_DOMAIN",
    "AUTONAVLOG_FIREBASE_APP_ID",
    "AUTONAVLOG_NAVMATE_URL",
)
PRESERVED_KEYS = (
    *AUTH_KEYS,
    "AUTONAVLOG_TRUSTED_LOCAL_IDENTITY",
    "AUTONAVLOG_SESSION_COOKIE_SECURE",
)
VOLUME = "autonavlog_autonavlog-data"


class InvalidConfiguration(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidConfiguration(message)


def check_config(config: dict) -> dict:
    require(config.get("name") == "autonavlog", "Compose project must be autonavlog.")
    require(
        set(config.get("services", {})) == {"autonavlog"}, "Expected only the autonavlog service."
    )
    service = config["services"]["autonavlog"]
    require(
        service.get("image") == "autonavlog:legacy", "Use the dedicated autonavlog:legacy image."
    )
    require(
        service.get("ports")
        == [
            {
                "mode": "ingress",
                "host_ip": "127.0.0.1",
                "target": 8000,
                "published": "8123",
                "protocol": "tcp",
            }
        ],
        "Expected loopback 127.0.0.1:8123 only.",
    )
    mounts = service.get("volumes", [])
    require(
        len(mounts) == 1
        and mounts[0].get("type") == "volume"
        and mounts[0].get("source") == "autonavlog-data"
        and mounts[0].get("target") == "/var/lib/autonavlog"
        and not mounts[0].get("read_only", False),
        "Unexpected data mount.",
    )
    require(
        config.get("volumes") == {"autonavlog-data": {"name": VOLUME, "external": True}},
        "Production must reuse the existing external data volume.",
    )
    env = service.get("environment", {})
    for key in AUTH_KEYS:
        require(bool(str(env.get(key) or "").strip()), f"Required setting missing: {key}.")
    require(
        not env.get("AUTONAVLOG_TRUSTED_LOCAL_IDENTITY"), "Trusted local identity must be empty."
    )
    require(
        env.get("AUTONAVLOG_SESSION_COOKIE_SECURE") == "true",
        "Secure session cookie must stay enabled.",
    )
    require(
        str(env.get("AUTONAVLOG_NAVMATE_URL", "")).rstrip("/") == "https://navmate.yuto24.com",
        "NavMate destination does not match the approved production URL.",
    )
    return service


def check_running(service: dict, container: dict) -> None:
    labels = container.get("Config", {}).get("Labels", {})
    require(
        labels.get("com.docker.compose.project") == "autonavlog"
        and labels.get("com.docker.compose.service") == "autonavlog",
        "Unexpected running service identity.",
    )
    require(
        container.get("State", {}).get("Running") is True,
        "Production service is not running; verify the baseline separately.",
    )
    current = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
    for key in PRESERVED_KEYS:
        before, after = current.get(key, ""), service["environment"].get(key, "")
        if key == "AUTONAVLOG_NAVMATE_URL":
            before, after = before.rstrip("/"), after.rstrip("/")
        require(before == after, f"Running setting differs: {key} (values withheld).")
    require(
        container.get("HostConfig", {}).get("PortBindings")
        == {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8123"}]},
        "Running port bindings differ.",
    )
    mounts = container.get("Mounts", [])
    require(
        len(mounts) == 1
        and mounts[0].get("Type") == "volume"
        and mounts[0].get("Name") == VOLUME
        and mounts[0].get("Destination") == "/var/lib/autonavlog"
        and mounts[0].get("RW") is True,
        "Running data volume differs.",
    )


def read_command(args: list[str], *, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    # Docker errors can contain resolved values. Do not relay stdout/stderr on failure.
    require(result.returncode == 0, f"Read-only {args[0]} check failed; details withheld.")
    return result.stdout.strip()


def check_source(expected_commit: str) -> None:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit)), "Expected a full commit SHA.")
    require(
        read_command(["git", "branch", "--show-current"]) == "release/1.x",
        "Use the dedicated release/1.x checkout.",
    )
    require(
        read_command(["git", "rev-parse", "HEAD"]) == expected_commit,
        "Checkout differs from the approved commit.",
    )
    require(
        not read_command(["git", "status", "--porcelain", "--untracked-files=all"]),
        "Tracked or untracked checkout changes must be resolved first.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-commit", required=True, help="Approved full release/1.x commit SHA"
    )
    args = parser.parse_args()
    try:
        check_source(args.expected_commit)
        env_file = ROOT / ".env"
        require(env_file.is_file(), "Canonical .env is missing.")
        require(
            env_file.stat().st_mode & 0o077 == 0,
            "Canonical .env must not be readable by group or others (chmod 600).",
        )
        # Compare ordinary commands with a single-file resolution unaffected by shell overrides.
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("AUTONAVLOG_", "COMPOSE_"))
        }
        canonical = json.loads(
            read_command(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env",
                    "-f",
                    "compose.yaml",
                    "config",
                    "--format",
                    "json",
                ],
                env=clean_env,
            )
        )
        effective = json.loads(read_command(["docker", "compose", "config", "--format", "json"]))
        require(
            canonical == effective,
            "Shell settings or Compose overrides differ from compose.yaml + .env.",
        )
        service = check_config(canonical)
        require(
            read_command(["docker", "volume", "inspect", VOLUME, "--format", "{{.Name}}"])
            == VOLUME,
            "Existing production volume is missing; do not create an empty replacement.",
        )
        containers = json.loads(read_command(["docker", "inspect", "autonavlog-autonavlog-1"]))
        require(len(containers) == 1, "Expected one running production container.")
        check_running(service, containers[0])
    except (InvalidConfiguration, OSError, ValueError, KeyError, TypeError) as error:
        message = (
            str(error)
            if isinstance(error, InvalidConfiguration)
            else "Could not read/validate deployment metadata; details withheld."
        )
        print(f"FAIL: {message}", file=sys.stderr)
        return 1
    print(
        "PASS: approved release/1.x, canonical Compose/.env, "
        "existing volume, bind and preserved auth settings."
    )
    print(
        "Read-only check only. Active calculations, backups, Firebase/Google acceptance "
        "and deployment approval remain separate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
