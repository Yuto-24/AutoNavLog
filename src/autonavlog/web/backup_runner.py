"""Internal subprocess entry point for the bounded guest calculation service."""

from __future__ import annotations

import os
import resource
import signal
import sys
from pathlib import Path


def configure_limits(wall_seconds: int = 120) -> None:
    # Applied before importing the numerical stack. The supervisor also imposes
    # a wall deadline and kills the entire process group on expiry.
    # An independent child alarm still expires if the supervising server crashes.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(wall_seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (110, 115))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2, 256 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"


def main() -> None:
    configure_limits()
    from autonavlog.forecast_backup import BackupRequest
    from autonavlog.weather.destination_taf import AviationWeatherTafProvider
    from autonavlog.weather.msm_adapter import MsmWeatherProvider
    from autonavlog.web.forecast_backup import MAX_REQUEST, calculator

    body = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(body) > MAX_REQUEST:
        raise ValueError("input exceeds budget")
    request = BackupRequest.model_validate_json(body)
    data_root, cache_root = (Path(value) for value in sys.argv[1:3])
    result = calculator(
        data_root, lambda: MsmWeatherProvider(cache_dir=cache_root), AviationWeatherTafProvider()
    )(request)
    output = result.model_dump_json()
    if len(output.encode()) > 2 * 1024 * 1024:
        raise ValueError("result exceeds budget")
    sys.stdout.write(output)


if __name__ == "__main__":
    main()
