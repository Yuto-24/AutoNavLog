"""Trusted static-only publication boundary, independent of optional usage collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, build_opener

try:
    from .operations import NoRedirect, release
except ImportError:
    from operations import NoRedirect, release

ORIGIN = "https://navmate.yuto24.com"
PROJECT = "navmate"
CONFIGURATION = (
    "VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID",
    "VITE_FIREBASE_APP_ID", "VITE_TAF_PROXY_URL", "VITE_LEGACY_MIGRATION_URL",
)
UPLOAD_CONFIG = {
    "name": PROJECT,
    "pages_build_output_dir": "./dist-static",
    "compatibility_date": "2026-09-19",
}


def identity(env: dict, event: dict, canonical: dict) -> str:
    """Use runner/event metadata and live approved configuration, never quota guesses."""
    repository = event.get("repository", {})
    if (repository.get("private") is not False
            or repository.get("full_name") != "Yuto-24/AutoNavLog"
            or env.get("GITHUB_REPOSITORY") != repository.get("full_name")):
        raise ValueError("Expected public AutoNavLog repository")
    if (env.get("GITHUB_ACTIONS") != "true"
            or env.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or env.get("RUNNER_OS") != "Linux"
            or env.get("RUNNER_ARCH") != "X64"
            or env.get("STATIC_RUNNER_CLASS") != "ubuntu-latest"):
        raise ValueError("Public standard GitHub-hosted ubuntu-latest runner required")
    approved = env.get("APPROVED_SHA", "")
    requested = env.get("REQUESTED_SHA") or approved
    if not re.fullmatch(r"[0-9a-f]{40}", approved) or requested != approved:
        raise ValueError("MSM renewal must use the approved full application SHA")
    if env.get("STATIC_ORIGIN") != ORIGIN:
        raise ValueError("Unexpected canonical publication origin")
    if not re.fullmatch(r"[0-9a-f]{32}", env.get("CLOUDFLARE_ACCOUNT_ID", "")):
        raise ValueError("Explicit Cloudflare account required")
    if canonical.get("commit") != approved or canonical.get("dirty") is not False:
        raise ValueError("Canonical release differs from approved clean application")
    configuration = {name: env[name] for name in CONFIGURATION if env.get(name)}
    if any(not configuration.get(name, "").strip() for name in CONFIGURATION[:-1]):
        raise ValueError("TAF and Sync public configuration must remain enabled")
    if configuration != canonical.get("configuration"):
        raise ValueError("Feed renewal must preserve approved public configuration")
    return approved


def static_sources(app: Path) -> None:
    """Reject implicit Functions and candidate Wrangler runtime configuration as well as dist."""
    for directory in (app, app / "web"):
        for name in ("functions", "_worker.js", "_worker.js.map"):
            if (directory / name).exists() or (directory / name).is_symlink():
                raise ValueError("Functions/Worker source in candidate upload context")
        for name in ("wrangler.toml", "wrangler.json", "wrangler.jsonc"):
            path = directory / name
            if not path.exists() and not path.is_symlink():
                continue
            if (path.is_symlink() or directory != app / "web" or name != "wrangler.jsonc"
                    or json.loads(path.read_text()) != UPLOAD_CONFIG):
                raise ValueError("Candidate Wrangler configuration contains unapproved settings")
        if (directory / ".wrangler/deploy/config.json").exists():
            raise ValueError("Wrangler redirected configuration is forbidden")


def artifact(app: Path, canonical: dict) -> dict:
    static_sources(app)
    dist = app / "web/dist-static"
    if dist.is_symlink():
        raise ValueError("Artifact root symlink forbidden")
    candidate = json.loads((dist / "release.json").read_text())
    if (candidate.get("commit") != canonical["commit"] or candidate.get("dirty") is not False
            or candidate.get("configuration") != canonical["configuration"]):
        raise ValueError("Candidate SHA/configuration differs from approved release")
    check_static(dist)
    return candidate


def check_static(dist: Path) -> None:
    """Independent inventory/private-material check; no candidate code is executed."""
    files = {}
    for path in sorted(dist.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in static artifact")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Non-regular static asset")
        name = path.relative_to(dist).as_posix()
        if (re.search(r"(^|/)(_worker\.js|functions|node_modules|\.git|\.env[^/]*|"
                      r"\.dev\.vars[^/]*)(/|$)", name)
                or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
            raise ValueError("Runtime/private file in static artifact")
        if path.stat().st_size > 25 * 1024**2:
            raise ValueError("Pages asset exceeds 25 MiB")
        payload = path.read_bytes()
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                     rb'"(?:private_key|client_secret)"\s*:', payload):
            raise ValueError("Secret material in static artifact")
        files[name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        if len(files) > 20_000:
            raise ValueError("Pages file count exceeds 20000")
    for name in ("index.html", "_headers", "404.html", "local/manifest.json",
                 "weather/msm/catalog.json", "release.json"):
        if name not in files:
            raise ValueError("Missing required static asset")
    candidate = json.loads((dist / "release.json").read_text())
    del files["release.json"]
    if candidate.get("dirty") is not False or files != candidate.get("files"):
        raise ValueError("Dirty artifact or inventory mismatch")
    manifest = json.loads((dist / "local/manifest.json").read_text())
    if (manifest["version"] != candidate["version"]
            or manifest["pyodideVersion"] != candidate["pyodideVersion"]):
        raise ValueError("Local runtime version mismatch")
    for name in [*manifest["wheels"], manifest["data"]]:
        if files.get("local/" + name, {}).get("sha256") != manifest["sha256"][name]:
            raise ValueError("Local asset hash mismatch")
    catalog = json.loads((dist / "weather/msm/catalog.json").read_text())
    expires = datetime.fromisoformat(catalog["expires_at"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires <= datetime.now(UTC) or not catalog["assets"]:
        raise ValueError("MSM catalog expired/empty")
    for asset in catalog["assets"]:
        recorded = files.get("weather/msm/" + asset["file"])
        if recorded != {"bytes": asset["bytes"], "sha256": asset["sha256"]}:
            raise ValueError("MSM asset mismatch")


def upload_context(app: Path, destination: Path, canonical: dict) -> None:
    artifact(app, canonical)
    # A new directory outside either checkout eliminates implicit functions discovery.
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(app / "web/dist-static", destination / "dist-static")
    (destination / "wrangler.json").write_text(json.dumps(UPLOAD_CONFIG) + "\n")
    verify_context(destination)


def verify_context(destination: Path) -> None:
    if destination.is_symlink() or set(p.name for p in destination.iterdir()) != {
        "dist-static", "wrangler.json"
    }:
        raise ValueError("Unexpected upload working-directory entry")
    config = destination / "wrangler.json"
    if config.is_symlink() or json.loads(config.read_text()) != UPLOAD_CONFIG:
        raise ValueError("Upload configuration is not purely static")
    if (destination / "dist-static").is_symlink():
        raise ValueError("Upload artifact root symlink forbidden")
    check_static(destination / "dist-static")


def verify_project(project: dict) -> None:
    """An existing production project/domain is required; never create a target implicitly."""
    if (project.get("name") != PROJECT or project.get("production_branch") != "main"
            or "navmate.yuto24.com" not in project.get("domains", [])):
        raise ValueError("Pages project/branch/canonical domain mismatch")
    configurations = project.get("deployment_configs")
    if (not isinstance(configurations, dict)
            or not isinstance(configurations.get("production"), dict)):
        raise ValueError("Pages production configuration unavailable")
    runtime = configurations["production"]
    # Existing Pages env_vars are not consumed by our isolated build or static upload.
    # Keep them untouched; verify_context separately forbids Functions and Worker code.
    for name in ("kv_namespaces", "durable_object_namespaces", "d1_databases", "r2_buckets",
                 "services", "queue_producers", "analytics_engine_datasets", "ai_bindings",
                 "vectorize_bindings", "hyperdrive_bindings"):
        if runtime.get(name):
            raise ValueError("Pages project runtime binding is forbidden")


def read_project(env: dict) -> None:
    url = ("https://api.cloudflare.com/client/v4/accounts/"
           + env["CLOUDFLARE_ACCOUNT_ID"] + "/pages/projects/" + PROJECT)
    try:
        request = Request(url, headers={"Authorization": "Bearer " + env["CLOUDFLARE_API_TOKEN"]})
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Oversize Pages response")
        value = json.loads(payload)
        if value.get("success") is not True:
            raise ValueError("Pages read failed")
        verify_project(value["result"])
    except Exception:
        # Never log native configuration, credentials or raw API errors.
        raise ValueError("Pages target read/validation failed; publication blocked") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--verify-context", type=Path)
    parser.add_argument("--project", action="store_true")
    args = parser.parse_args()
    if args.verify_context:
        verify_context(args.verify_context)
    else:
        canonical = release(os.environ["STATIC_ORIGIN"], os.environ["APPROVED_SHA"])
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        sha = identity(dict(os.environ), event, canonical)
        if args.source:
            static_sources(args.source)
        if args.app:
            artifact(args.app, canonical)
        if args.stage:
            upload_context(args.app, args.stage, canonical)
        print(json.dumps({"status": "OK", "gate": "static-only", "commit": sha,
                          "optional_usage": "UNKNOWN", "account_billing": "UNKNOWN"}))
    if args.project:
        read_project(dict(os.environ))


if __name__ == "__main__":
    main()
