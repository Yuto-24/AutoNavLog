"""Build the ordinary Python wheel and bundled data for the static Pyodide PoC."""

import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
target = root / "web/public-local/local"
target.mkdir(parents=True, exist_ok=True)
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
    shutil.copyfile(wheel, target / "autonavlog.whl")
with ZipFile(target / "data.zip", "w", ZIP_DEFLATED) as archive:
    for folder in ("performance", "reference"):
        for path in sorted((root / "data" / folder).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root))
