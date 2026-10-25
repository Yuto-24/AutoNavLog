"""All feed renewals require new complete Runs; recheck canonical before upload."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from jma_gpv_weather import MsmClient  # noqa: E402
from jma_gpv_weather.msm.spec import parse_listing  # noqa: E402

from autonavlog.weather.local_msm import WeatherCatalog  # noqa: E402
from scripts.prepare_msm_feed import acquire_listings, forecast_requirements  # noqa: E402
from scripts.static_ops.operations import fetch  # noqa: E402
from scripts.static_ops.refresh_feed import PUBLICATION_GENERATION_HOURS  # noqa: E402


def preflight(origin: str, now: datetime | None = None) -> dict:
    """Read canonical and upstream metadata; admit only a newer complete Run or a healthy no-op."""
    started = now or datetime.now(UTC)
    catalog = WeatherCatalog.model_validate_json(fetch(
        origin.rstrip("/") + "/weather/msm/catalog.json", 8 * 1024 * 1024))
    if (not catalog.assets or not catalog.listings or catalog.generated_at > started
            or catalog.expires_at - catalog.generated_at != timedelta(hours=6)):
        raise ValueError("Invalid production catalog lifecycle")
    runs = [datetime.strptime(a.run, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            for a in catalog.assets]
    if any(run > catalog.generated_at or run.hour % 3 or run.minute or run.second for run in runs):
        raise ValueError("Invalid production catalog Run")
    latest = max(runs)
    catalog_runs = {file.run_utc for url, listing in catalog.listings.items()
                    for file in parse_listing(listing, url)}
    if not set(runs) <= catalog_runs:
        raise ValueError("Production catalog lacks retained Run source listings")
    client = MsmClient()
    requirements = forecast_requirements(
        started.replace(minute=0, second=0, microsecond=0), PUBLICATION_GENERATION_HOURS)
    listings = acquire_listings(client, requirements, require_content=True)
    advertised = set()
    for url, listing in listings.items():
        files = parse_listing(listing, url)
        if listing.strip() and not files:
            raise ValueError("Unrecognized upstream listing; absence is not confirmed")
        advertised.update(file.run_utc for file in files)
    if any(run > started or run.hour % 3 or run.minute or run.second for run in advertised):
        raise ValueError("Invalid upstream Run")
    # The real library checks all 25 hours and interpolation brackets from source files.
    # Empty/unavailable/incomplete discovery raises; it must never become NO_NEW_RUN.
    complete = client.discover_runs(requirements, listings=listings)
    newest = max(run.run_utc for run in complete)
    checked = now or datetime.now(UTC)
    remaining = (catalog.expires_at - checked).total_seconds()
    if newest <= latest:
        if any(run > latest for run in advertised):
            raise ValueError("Newer upstream Run is incomplete")
        if newest < latest:
            raise ValueError("Published latest Run missing from complete upstream discovery")
        # The browser discovers coverage from retained catalog listings. Upstream
        # completeness alone cannot establish that the published metadata is usable.
        published_requirements = forecast_requirements(
            checked.replace(minute=0, second=0, microsecond=0), 24)
        published_complete = client.discover_runs(
            published_requirements, listings=catalog.listings)
        if latest not in {run.run_utc for run in published_complete}:
            raise ValueError("Published latest Run has incomplete catalog coverage")
        if remaining < 7200:
            raise ValueError(
                "No new Run and production freshness warning (less than two hours or expired)")
    proceed = newest > latest
    return {"status": "NEW_RUN" if proceed else "NO_NEW_RUN", "proceed": proceed,
            "published_run": latest.strftime("%Y%m%d%H%M%S"),
            "upstream_run": newest.strftime("%Y%m%d%H%M%S"),
            "generation_hours": PUBLICATION_GENERATION_HOURS,
            "weather_seconds_remaining": remaining, "current_catalog_expired": remaining <= 0}


def require_newer_candidate(origin: str, candidate: Path, now: datetime | None = None) -> dict:
    """Run inside shared publication concurrency immediately before upload.

    Existing publication/validate_feed gates validate identity, bytes, coverage and
    lifetime. This final canonical read prevents a race from renewing the same Run.
    """
    checked = now or datetime.now(UTC)
    published = WeatherCatalog.model_validate_json(fetch(
        origin.rstrip("/") + "/weather/msm/catalog.json", 8 * 1024 * 1024))
    proposed = WeatherCatalog.model_validate_json(candidate.read_text())
    for catalog in (published, proposed):
        if (not catalog.assets or catalog.generated_at > checked
                or catalog.expires_at - catalog.generated_at != timedelta(hours=6)):
            raise ValueError("Invalid catalog before publication")
        for asset in catalog.assets:
            run = datetime.strptime(asset.run, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if run > catalog.generated_at or run.hour % 3 or run.minute or run.second:
                raise ValueError("Invalid Run before publication")
    previous = max(asset.run for asset in published.assets)
    newest = max(asset.run for asset in proposed.assets)
    if newest <= previous:
        raise ValueError("Canonical already has this or a newer Run; no timestamp-only renewal")
    if proposed.expires_at <= checked:
        raise ValueError("Candidate expired before publication")
    return {"status": "NEWER_CANDIDATE", "published_run": previous, "candidate_run": newest}


def main() -> None:
    """Emit discovery outputs or enforce the final newer-candidate check before upload."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    if args.candidate:
        print(json.dumps(require_newer_candidate(args.origin, args.candidate), indent=2))
        return
    if args.github_output is None:
        parser.error("--github-output is required for discovery")
    report = preflight(args.origin)
    # Only a complete successful verdict may enable heavy work or a healthy no-op.
    with args.github_output.open("a") as output:
        output.write(f"proceed={str(report['proceed']).lower()}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
