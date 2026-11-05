"""Check upload identity/configuration, or exact canonical inventory after upload."""

import argparse
import json
import os
import re
import ssl
import sys
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from operations import (
    StaticRedirectError,
    VerificationError,
    fetch,
    monitor,
    release,
    static_request,
)


def verify_payloads(path: str) -> int:
    """One complete MSM publication attempt: 1 retries, 2 stops the poll."""
    from freshness import canonical

    try:
        with open(path) as stream:
            candidate = json.load(stream)
        source = os.environ["EXPECTED_SOURCE"]
        origin = os.environ["STATIC_ORIGIN"].rstrip("/")
        static_request(origin + "/release.json")
        parsed = urlsplit(origin)
        if not parsed.hostname or parsed.port == 0:
            raise ValueError("Invalid origin")
        if (not re.fullmatch(r"[a-f0-9]{40}", source)
                or candidate["commit"] != source or candidate["dirty"] is not False):
            raise ValueError("Invalid candidate identity")
        files = candidate["files"]
        for name in ("weather/msm/catalog.json", "weather/msm/freshness.json"):
            if name not in files:
                raise ValueError("Missing candidate metadata")
        for record in files.values():
            if (set(record) != {"bytes", "sha256"} or type(record["bytes"]) is not int
                    or record["bytes"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])):
                raise ValueError("Invalid candidate inventory")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Invalid local candidate or publication configuration", file=sys.stderr)
        return 2

    target = "release.json"

    def fetch_payload(url, limit):
        nonlocal target
        target = url.removeprefix(origin + "/")
        print(f"Verifying target={target} commit={source}", file=sys.stderr, flush=True)
        return fetch(url, limit)

    try:
        result = canonical(origin, source, fetch_payload, candidate=candidate)
    except HTTPError as error:
        print(f"target={target} HTTP status={error.code}", file=sys.stderr)
        return 1 if error.code in {404, 408, 429} or 500 <= error.code < 600 else 2
    except (StaticRedirectError, ssl.SSLCertVerificationError):
        print(f"target={target} redirect or TLS verification rejected", file=sys.stderr)
        return 2
    except URLError as error:
        print(f"target={target} transport failure", file=sys.stderr)
        return 2 if isinstance(error.reason, ssl.SSLCertVerificationError) else 1
    except VerificationError as error:
        print(f"target={target} commit={source} {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        print(
            f"target={target} commit={source} invalid response/transport: {type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result))
    return 0


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("candidate")
parser.add_argument("--remote", action="store_true")
parser.add_argument("--verify-payloads", action="store_true")
args = parser.parse_args()
if args.verify_payloads:
    if not args.remote:
        parser.error("--verify-payloads requires --remote")
    raise SystemExit(verify_payloads(args.candidate))
with open(args.candidate) as stream:
    candidate = json.load(stream)
if candidate["commit"] != os.environ["EXPECTED_SOURCE"] or candidate["dirty"] is not False:
    raise ValueError("Candidate source differs from reviewed source")
origin = os.environ["STATIC_ORIGIN"]
if args.remote:
    canonical = json.loads(fetch(origin.rstrip("/") + "/release.json", 8 * 1024 * 1024))
    if canonical != candidate:
        raise ValueError("Canonical release does not match uploaded artifact inventory")
    weather = monitor(
        origin, datetime.now(UTC), expected=candidate["files"]["weather/msm/catalog.json"]
    )
    print(json.dumps({"commit": candidate["commit"], **weather}))
elif os.environ["EXPECTED_SOURCE"] == os.environ["APPROVED_SHA"]:
    canonical = release(origin, os.environ["APPROVED_SHA"])
    if canonical["configuration"] != candidate["configuration"]:
        raise ValueError("Feed refresh must preserve approved public configuration")
