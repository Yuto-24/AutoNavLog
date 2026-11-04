"""New tooling must keep rebuilding both older and current approved applications."""

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.static_ops.local_runtime import Settings, Updater

ROOT = Path(__file__).resolve().parents[2]
OLD = "jma_gpv_weather-0.5.0-py3-none-any.whl"
NEW = "jma_gpv_weather-0.6.0-py3-none-any.whl"


def candidate(root, wheels):
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    for name in wheels:
        (vendor / name).touch()


def install_script(kind):
    source = (ROOT / (".github/workflows/static-production.yml" if kind == "weather" else
                      "scripts/static_ops/build_app_release.sh")).read_text()
    # Run the actual candidate-install preamble without build/network operations.
    return source[source.index("set -- vendor/"):source.index("npm --prefix web ci")]


@pytest.mark.parametrize("kind", ["weather", "release"])
@pytest.mark.parametrize("wheels", [(OLD,), (NEW,), (), (OLD, NEW)])
def test_candidate_shell_uses_its_single_wheel_and_retains_dependency_resolution(
    tmp_path, kind, wheels,
):
    candidate(tmp_path, wheels)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_python = binaries / "python"
    fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CANDIDATE_INSTALL_RECORD"\n')
    fake_python.chmod(0o755)
    record = tmp_path / "install.txt"
    result = subprocess.run(["/bin/sh", "-eu", "-c", install_script(kind)], cwd=tmp_path,
                            env={**os.environ, "PATH": str(binaries),
                                 "CANDIDATE_INSTALL_RECORD": str(record)}, check=False)
    if len(wheels) != 1:
        assert result.returncode != 0
        assert not record.exists()
    else:
        assert result.returncode == 0
        assert record.read_text().splitlines() == [
            "-m", "pip", "install", "setuptools>=69", "wheel", f"vendor/{wheels[0]}", ".[msm,test]",
        ]


@pytest.mark.parametrize("wheels", [(OLD,), (NEW,), (), (OLD, NEW)])
def test_local_updater_resolves_candidate_wheel_before_build(tmp_path, wheels):
    updater = Updater(Settings(tmp_path / "source", tmp_path / "state", tmp_path / "published"))
    job = updater.s.state / "work/candidate-install"
    job.mkdir(parents=True)
    updater.clone = lambda source, app, sha: candidate(app, wheels)
    calls = []

    class InstallationReached(Exception):
        pass

    def run(args, cwd, **kwargs):
        calls.append(args)
        if args[1:4] == ["-m", "pip", "install"]:
            raise InstallationReached
        return ""

    updater.run = run
    with pytest.raises(InstallationReached if len(wheels) == 1 else ValueError):
        updater.build("main", "a" * 40, updater.s.source, job)
    installs = [args for args in calls if args[1:4] == ["-m", "pip", "install"]]
    if len(wheels) != 1:
        assert not installs
    else:
        assert installs[0][-2:] == [f"vendor/{wheels[0]}", ".[msm,test]"]


def test_pip_rejects_single_weather_wheel_that_disagrees_with_candidate_pin(tmp_path):
    # A self-contained distribution avoids assuming a global source-build backend
    # in Docker. Its optional dependency is the candidate's exact msm constraint.
    candidate_wheel = tmp_path / "candidate_weather_pin_fixture-0.0.0-py3-none-any.whl"
    metadata = "candidate_weather_pin_fixture-0.0.0.dist-info/"
    with ZipFile(candidate_wheel, "w") as wheel:
        wheel.writestr(metadata + "METADATA", "Metadata-Version: 2.1\n"
                       "Name: candidate-weather-pin-fixture\nVersion: 0.0.0\n"
                       "Provides-Extra: msm\nProvides-Extra: test\n"
                       'Requires-Dist: jma-gpv-weather==0.5.0; extra == "msm"\n')
        wheel.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\n"
                       "Root-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(metadata + "RECORD", "")
    result = subprocess.run([
        sys.executable, "-m", "pip", "install", "--dry-run", "--no-index",
        "--disable-pip-version-check", str(ROOT / "vendor" / NEW),
        str(candidate_wheel) + "[msm,test]",
    ], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "ResolutionImpossible" in result.stderr
