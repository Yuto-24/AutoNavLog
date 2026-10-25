"""Admission limits run after GitHub concurrency, independently of eventual KV."""
from datetime import UTC, datetime, timedelta

import pytest

from scripts.static_ops.external_trigger_gate import validate

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
TARGET = "20261025060000"
EVENT = {"inputs": {"feed_refresh": "true", "publish": "true", "observed_run": TARGET}}
ENV = {"GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"}


def run(id=100, age=0, target=TARGET):
    return {"id": id, "created_at": (NOW - timedelta(minutes=age)).isoformat(),
            "display_title": "msm-fallback/" + target}


def test_admits_single_request():
    validate(EVENT, ENV, [run()], NOW)


@pytest.mark.parametrize("inputs", [
    {"source_sha": "a" * 40}, {"publish": "false"}, {"feed_refresh": "false"},
    {"observed_run": "bad"}, {"observed_run": "20261025150000"},
])
def test_external_request_cannot_select_app_skip_publish_or_use_invalid_run(inputs):
    with pytest.raises(ValueError):
        validate({"inputs": {**EVENT["inputs"], **inputs}}, ENV, [run()], NOW)


def test_queued_duplicate_cooldown_rerun_and_missing_history():
    for runs in ([run(), run(99, 30)], [], [run(99)]):
        with pytest.raises(ValueError):
            validate(EVENT, ENV, runs, NOW)
    with pytest.raises(ValueError):
        validate(EVENT, {**ENV, "GITHUB_RUN_ATTEMPT": "2"}, [run()], NOW)


def test_daily_and_per_run_caps():
    with pytest.raises(ValueError, match="daily"):
        validate(EVENT, ENV, [run()] + [run(i, 120, "20261025000000") for i in range(8)], NOW)
    with pytest.raises(ValueError, match="per-Run"):
        validate(EVENT, ENV, [run(), run(98, 120), run(99, 240)], NOW)
    validate(EVENT, ENV, [run(), run(99, 120)], NOW)
