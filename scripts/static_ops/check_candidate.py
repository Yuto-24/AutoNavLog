"""Check upload identity/configuration, or exact canonical inventory after upload."""

import argparse
import json
import os

from operations import fetch, release

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("candidate")
parser.add_argument("--remote", action="store_true")
args = parser.parse_args()
with open(args.candidate) as stream:
    candidate = json.load(stream)
if candidate["commit"] != os.environ["EXPECTED_SOURCE"] or candidate["dirty"] is not False:
    raise ValueError("Candidate source differs from reviewed source")
origin = os.environ["STATIC_ORIGIN"]
if args.remote:
    canonical = json.loads(fetch(origin.rstrip("/") + "/release.json", 8 * 1024 * 1024))
    if canonical != candidate:
        raise ValueError("Canonical release does not match uploaded artifact inventory")
    print(json.dumps({"status": "OK", "commit": candidate["commit"]}))
elif os.environ["EXPECTED_SOURCE"] == os.environ["APPROVED_SHA"]:
    canonical = release(origin, os.environ["APPROVED_SHA"])
    if canonical["configuration"] != candidate["configuration"]:
        raise ValueError("Feed refresh must preserve approved public configuration")
