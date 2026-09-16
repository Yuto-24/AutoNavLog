from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .runtime import WebRuntimeConfig, environment_bool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local NavMate Web application.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(".autonavlog-data"),
    )
    parser.add_argument(
        "--weather",
        choices=("fake", "msm"),
        default="fake",
        help="fake is deterministic development weather and blocks transfer output",
    )
    parser.add_argument("--msm-cache", type=Path)
    parser.add_argument("--maximum-sessions", type=int, default=256)
    parser.add_argument(
        "--trusted-local-identity",
        default=os.environ.get("AUTONAVLOG_TRUSTED_LOCAL_IDENTITY"),
        help="trusted local-only identity; leave unset behind Cloudflare Access",
    )
    parser.add_argument(
        "--session-cookie-secure",
        action=argparse.BooleanOptionalAction,
        default=environment_bool("AUTONAVLOG_SESSION_COOKIE_SECURE", default=True),
        help="mark session cookies Secure; disable only for trusted HTTP LAN development",
    )
    parser.add_argument(
        "--cloudflare-team-domain",
        default=os.environ.get("AUTONAVLOG_CLOUDFLARE_TEAM_DOMAIN"),
        help="expected https://<team>.cloudflareaccess.com JWT issuer",
    )
    parser.add_argument(
        "--cloudflare-access-audience",
        default=os.environ.get("AUTONAVLOG_CLOUDFLARE_ACCESS_AUDIENCE"),
        help="expected Cloudflare Access application AUD tag",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    config = WebRuntimeConfig(
        data_root=args.data_root,
        storage_root=args.storage_root,
        weather_mode=args.weather,
        msm_cache_dir=args.msm_cache,
        maximum_sessions=args.maximum_sessions,
        trusted_local_identity=args.trusted_local_identity,
        cloudflare_team_domain=args.cloudflare_team_domain,
        session_cookie_secure=args.session_cookie_secure,
        cloudflare_access_audience=args.cloudflare_access_audience,
    )
    uvicorn.run(
        create_app(config),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        server_header=False,
    )


if __name__ == "__main__":
    main()
