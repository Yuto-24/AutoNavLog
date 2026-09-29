"""Poll exact canonical inventory after upload, with a bounded wall-clock budget."""

import argparse
import math
import subprocess
import sys
import time
from pathlib import Path


def wait_for_candidate(
    candidate: Path,
    *,
    timeout: float = 300,
    interval: float = 10,
    attempt_timeout: float = 30,
) -> None:
    for value in (timeout, interval, attempt_timeout):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Polling budgets must be finite positive seconds")
    command = [
        sys.executable,
        str(Path(__file__).with_name("check_candidate.py")),
        str(candidate),
        "--remote",
    ]
    deadline = time.monotonic() + timeout
    attempt = 0
    while (remaining := deadline - time.monotonic()) > 0:
        attempt += 1
        try:
            # A process timeout also bounds slow response bodies, unlike socket timeouts.
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=min(attempt_timeout, remaining)
            )
        except subprocess.TimeoutExpired:
            reason = "attempt timed out"
        else:
            if result.returncode == 0 and time.monotonic() < deadline:
                print(result.stdout, end="")
                print(f"Canonical inventory verified after {attempt} attempt(s)", file=sys.stderr)
                return
            reason = (
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else f"checker exit {result.returncode} (or deadline reached)"
            )
        print(f"Canonical verification attempt {attempt} failed: {reason}", file=sys.stderr)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    raise TimeoutError(f"Canonical inventory verification timed out after {timeout:g}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--attempt-timeout-seconds", type=float, default=30)
    args = parser.parse_args()
    wait_for_candidate(
        args.candidate,
        timeout=args.timeout_seconds,
        interval=args.interval_seconds,
        attempt_timeout=args.attempt_timeout_seconds,
    )


if __name__ == "__main__":
    main()
