from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pytest_shard", ROOT / "scripts/pytest_shard.py")
assert spec and spec.loader
shard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shard)


def test_partition_keeps_every_file_once_and_balances_measured_work():
    durations = {"a": 10, "b": 8, "c": 3, "d": 1}
    bins = shard.partition(["a", "b", "a", "c", "d"], durations, 2)
    assert bins == [["a", "d"], ["b", "c"]]
    assert sorted(file for group in bins for file in group) == sorted(durations)


def test_partition_includes_new_files_and_is_order_independent():
    files = ["old", "new", "another"]
    bins = shard.partition(files, {"old": 10}, 2)
    assert bins == shard.partition(list(reversed(files)), {"old": 10}, 2)
    assert sorted(file for group in bins for file in group) == sorted(files)


def test_partition_rejects_invalid_count():
    with pytest.raises(ValueError):
        shard.partition(["a"], {}, 0)


@pytest.mark.parametrize("mode", ["valid", "missing", "duplicate", "different", "coverage"])
def test_manifest_gate(tmp_path, mode):
    import json

    validator_spec = importlib.util.spec_from_file_location(
        "check_pytest_shards", ROOT / "scripts/check_pytest_shards.py"
    )
    assert validator_spec and validator_spec.loader
    validator = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator)
    for index in (1, 2):
        folder = tmp_path / str(index)
        folder.mkdir()
        manifest = {"shard": index, "count": 2, "collected": ["a", "b"],
                    "selected": ["a" if index == 1 else "b"]}
        if mode == "missing" and index == 2:
            continue
        if mode == "duplicate" and index == 2:
            manifest["selected"] = ["a"]
        if mode == "different" and index == 2:
            manifest["collected"] = ["b"]
        (folder / "collection.json").write_text(json.dumps(manifest))
        if mode != "coverage" or index != 2:
            (folder / ".coverage").touch()
    if mode == "valid":
        validator.check(tmp_path, 2)
    else:
        with pytest.raises(ValueError):
            validator.check(tmp_path, 2)


@pytest.mark.parametrize("value", ["0/2", "3/2", "1/0", "one/two", "1", "1/2/3"])
def test_plugin_rejects_invalid_shard(monkeypatch, value):
    monkeypatch.setenv("CI_PYTEST_SHARD", value)
    with pytest.raises(pytest.UsageError):
        shard.pytest_collection_modifyitems(None, [])


def test_workflow_preserves_aggregate_gates_and_all_browser_suites():
    workflow = (ROOT / ".github/workflows/test.yml").read_text()
    assert "needs: [release-sources, pytest-tests]" in workflow
    assert "needs: [release-sources, local-browser-tests]" in workflow
    for job, following in (("pytest", "runtime"), ("local-browser", "account-sync-tests")):
        block = workflow.split(f"  {job}:\n", 1)[1].split(f"  {following}:\n", 1)[0]
        assert 'run: test "$RESULT" = success' in block
        assert "if: ${{ always() && (needs.release-sources.result" in block
    assert "fail-fast: false" in workflow
    for command in (
        "npm --prefix web run test:auth",
        "npm --prefix web run test:local -- --workers=2",
        "playwright.navlog-scroll.config.ts --workers=2",
        "playwright.browser-acceptance.config.ts --workers=2",
        "npm --prefix web run test:taf",
    ):
        assert workflow.count(command) == 1
    package = (ROOT / "web/package.json").read_text()
    assert "saved Run stays fixed on first click and refreshes snapshot on second click" in package
    assert "suite: [local, acceptance, focused-ux, clipboard]" in workflow
    assert '"web/playwright.${{ matrix.suite }}.config.ts" --workers=2' in workflow
    assert "python scripts/check_pytest_shards.py coverage-shards 2" in workflow
    assert "python -m coverage combine coverage-shards/*/.coverage" in workflow


def test_new_file_does_not_inherit_the_outlier_weight():
    durations = {"heavy": 400, "a": 1, "b": 1, "c": 1}
    bins = shard.partition([*durations, "new"], durations, 2)
    assert bins[0] == ["heavy"]
    assert sorted(bins[1]) == ["a", "b", "c", "new"]


def test_junit_weights_resolve_unittest_class_to_its_source(tmp_path):
    timing_spec = importlib.util.spec_from_file_location(
        "pytest_timing_weights", ROOT / "scripts/pytest_timing_weights.py"
    )
    assert timing_spec and timing_spec.loader
    timing = importlib.util.module_from_spec(timing_spec)
    timing_spec.loader.exec_module(timing)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").touch()
    report = tmp_path / "report.xml"
    report.write_text('<testsuite><testcase classname="tests.test_example.First" time="1.5"/>'
                      '<testcase classname="tests.test_example.Second" time="2"/></testsuite>')
    assert timing.weights(report, tmp_path) == {"tests/test_example.py": 3.5}
    report.write_text('<testsuite><testcase classname="tests.missing.Class" time="1"/></testsuite>')
    with pytest.raises(ValueError, match="No source file"):
        timing.weights(report, tmp_path)
