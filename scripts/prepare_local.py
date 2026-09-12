"""Build the ordinary Python wheel and bundled data for the static Pyodide PoC."""

import hashlib
import json
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

    for path in sorted((root / "tests/fixtures/msm").glob("*")):
        if path.suffix in {".npz", ".json"}:
            archive.write(path, Path("data/msm-fixture") / path.name)
msm_wheel = root / "vendor/jma_msm_wind-0.2.1-py3-none-any.whl"
shutil.copyfile(msm_wheel, target / msm_wheel.name)
assets = [application_wheel, msm_wheel.name, "data.zip"]
manifest = {"wheels": assets[:2], "data": "data.zip", "sha256": {
    name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in assets
}}
(target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
