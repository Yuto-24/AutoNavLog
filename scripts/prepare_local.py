"""Build the ordinary Python wheel and bundled data for the static Pyodide PoC."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
target = root / "web/public-local/local"
target.mkdir(parents=True, exist_ok=True)
# This ignored directory contains generated assets only; retire obsolete wheel names.
for stale_wheel in target.glob("*.whl"):
    stale_wheel.unlink()
shutil.copytree(root / "web/public", target.parent, dirs_exist_ok=True)
with TemporaryDirectory() as temporary:
    staging = Path(temporary) / "source"
    staging.mkdir()
    for name in ("pyproject.toml", "VERSION", "README.md"):
        shutil.copyfile(root / name, staging / name)
    shutil.copytree(
        root / "src/autonavlog",
        staging / "src/autonavlog",
        ignore=shutil.ignore_patterns("static", "__pycache__"),
    )
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", temporary],
        cwd=staging,
        check=True,
    )
    wheel = next(Path(temporary).glob("*.whl"))
    shutil.copyfile(wheel, target / wheel.name)
    application_wheel = wheel.name
with ZipFile(target / "data.zip", "w", ZIP_DEFLATED) as archive:
    for folder in ("performance", "reference"):
        for path in sorted((root / "data" / folder).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root))

    if os.environ.get("AUTONAVLOG_TEST_FIXTURES") == "1":
        for path in sorted((root / "tests/fixtures/msm").glob("*")):
            if path.suffix in {".npz", ".json"}:
                archive.write(path, Path("data/msm-fixture") / path.name)
msm_wheel = root / "vendor/jma_gpv_weather-0.5.0-py3-none-any.whl"
shutil.copyfile(msm_wheel, target / msm_wheel.name)
assets = [application_wheel, msm_wheel.name, "data.zip"]
manifest = {
    "wheels": assets[:2],
    "data": "data.zip",
    "sha256": {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in assets},
}
(target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

# A production feed is generated independently of the app build/ordinary CI.
# Retire only these generated Weather assets so an old feed cannot leak into a build.
weather_target = target.parent / "weather/msm"
weather_target.mkdir(parents=True, exist_ok=True)
for stale in [weather_target / "catalog.json", *weather_target.glob("*.npz")]:
    stale.unlink(missing_ok=True)
if feed_directory := os.environ.get("AUTONAVLOG_MSM_FEED"):
    from autonavlog.weather.local_msm import WeatherCatalog

    feed = Path(feed_directory).resolve()
    catalog = WeatherCatalog.model_validate_json((feed / "catalog.json").read_text())
    for asset in catalog.assets:
        source = feed / asset.file
        if (
            source.stat().st_size != asset.bytes
            or hashlib.sha256(source.read_bytes()).hexdigest() != asset.sha256
        ):
            raise ValueError(f"MSM feed asset integrity failure: {asset.file}")
        shutil.copyfile(source, weather_target / asset.file)
    shutil.copyfile(feed / "catalog.json", weather_target / "catalog.json")
