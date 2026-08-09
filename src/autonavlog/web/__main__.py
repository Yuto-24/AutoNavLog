from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import uvicorn

from .app import create_app
from .runtime import WeatherMode, WebRuntimeConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local AutoNavLog Web application.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(".autonavlog-data"),
    )
    parser.add_argument(
        "--weather",
        choices=("fake", "msm", "msm-metar"),
        default="fake",
        help="fake is deterministic development weather and blocks transfer output",
    )
    parser.add_argument("--msm-cache", type=Path)
    parser.add_argument("--terrain-cache", type=Path)
    parser.add_argument("--log-level", default="info")
    return parser


def main() -> None:
    args = _parser().parse_args()
    config = WebRuntimeConfig(
        data_root=args.data_root,
        storage_root=args.storage_root,
        weather_mode=cast(WeatherMode, args.weather),
        msm_cache_dir=args.msm_cache,
        terrain_cache_path=args.terrain_cache,
    )
    uvicorn.run(
        create_app(config),
        host=str(args.host),
        port=int(args.port),
        log_level=str(args.log_level),
        server_header=False,
    )


if __name__ == "__main__":
    main()
