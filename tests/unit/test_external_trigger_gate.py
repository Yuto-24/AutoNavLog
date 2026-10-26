"""Admission limits run after GitHub concurrency, independently of eventual KV."""
from datetime import UTC, datetime, timedelta

import pytest

from scripts.static_ops.external_trigger_gate import validate

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
TARGET = "20261025060000"
EVENT = {"inputs": {"feed_refresh": "true", "publish": "true", "observed_run": TARGET}}
ENV = {"GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"}


def run(id=100, age=0, target=TARGET):
    """Build an external-request history entry relative to the fixed test clock."""
    return {"id": id, "created_at": (NOW - timedelta(minutes=age)).isoformat(),
            "display_title": "msm-fallback/" + target, "status": "completed"}


def test_admits_single_request():
    """A first feed-only request present in Actions history is admitted."""
    validate(EVENT, ENV, [run()], NOW)


@pytest.mark.parametrize("inputs", [
    {"source_sha": "a" * 40}, {"publish": "false"}, {"feed_refresh": "false"},
    {"observed_run": "bad"}, {"observed_run": "20261025150000"},
])
def test_external_request_cannot_select_app_skip_publish_or_use_invalid_run(inputs):
    """Reject app selection, disabled publication and malformed or future observed Runs."""
    with pytest.raises(ValueError):
        validate({"inputs": {**EVENT["inputs"], **inputs}}, ENV, [run()], NOW)


def test_queued_duplicate_cooldown_rerun_and_missing_history():
    """Missing history, a recent competing request and reruns must fail closed."""
    for runs in ([run(), run(99, 30)], [], [run(99)]):
        with pytest.raises(ValueError):
            validate(EVENT, ENV, runs, NOW)
    with pytest.raises(ValueError):
        validate(EVENT, {**ENV, "GITHUB_RUN_ATTEMPT": "2"}, [run()], NOW)


def test_daily_and_per_run_caps():
    """Enforce daily and per-Run budgets while allowing a request below both limits."""
    with pytest.raises(ValueError, match="daily"):
        validate(EVENT, ENV, [run()] + [run(i, 120, "20261025000000") for i in range(8)], NOW)
    with pytest.raises(ValueError, match="per-Run"):
        validate(EVENT, ENV, [run(), run(98, 120), run(99, 240)], NOW)
    validate(EVENT, ENV, [run(), run(99, 120)], NOW)


def test_request_identity_is_not_an_observed_forecast_run():
    """New requests carry an opaque ID and retain accounting for no-op executions."""
    event = {"inputs": {"feed_refresh": "true", "publish": "true", "request_id": "1792929600000"}}
    own = {**run(), "display_title": "msm-request/1792929600000", "status": "in_progress"}
    validate(event, ENV, [own], NOW)
    for extra in ({"observed_run": TARGET}, {"request_id": TARGET}, {"request_id": "bad"}):
        with pytest.raises(ValueError):
            validate({"inputs": {**event["inputs"], **extra}}, ENV, [own], NOW)
    with pytest.raises(ValueError, match="Duplicate"):
        validate(event, ENV, [own, {**own, "id": 99}], NOW)
    noops = [{**run(i, 120), "display_title": "msm-request/" + str(i)} for i in range(8)]
    with pytest.raises(ValueError, match="daily"):
        validate(event, ENV, [own, *noops], NOW)


@pytest.mark.parametrize("status", ["waiting", "pending", "queued", "requested", "in_progress"])
def test_actions_rechecks_active_and_approval_states(status):
    """A competing run must block work even if Worker KV was stale."""
    with pytest.raises(ValueError, match="active"):
        validate(EVENT, ENV, [run(), {**run(99, 120), "status": status}], NOW)


def test_detected_run_attempts_are_durable_and_separate_from_request_count():
    """Actual discovery job names bound retries, including failures and legacy requests."""
    from scripts.static_ops.external_trigger_gate import detected_limit
    rows = [run(), {**run(98, 120), "display_title": "msm-request/one"},
            {**run(99, 240), "display_title": "msm-request/two"}]
    def jobs(run_id):
        return [{"name": "msm-detected/" + TARGET}]
    with pytest.raises(ValueError, match="per-Run"):
        detected_limit(TARGET, rows, 100, jobs)
    detected_limit(TARGET, rows[:2], 100, jobs)
    detected_limit(TARGET, rows, 100, lambda _: [{"name": "preflight"}])
    with pytest.raises(TimeoutError):
        detected_limit(TARGET, rows, 100, lambda _: (_ for _ in ()).throw(TimeoutError()))


def test_bounded_history_rejects_truncation_and_duplicates(monkeypatch):
    """Incomplete evidence cannot replenish the independent Actions budget."""
    import io
    import json
    from urllib.request import Request

    from scripts.static_ops import external_trigger_gate as gate
    for value in ({"total_count": 101, "workflow_runs": []},
                  {"total_count": 2, "workflow_runs": [run(), run()]}):
        class Opener:
            def open(self, *args, captured=value, **kwargs):
                return io.BytesIO(json.dumps(captured).encode())
        monkeypatch.setattr(gate, "build_opener", lambda *_: Opener())
        with pytest.raises(ValueError, match="Incomplete"):
            gate.read_list(Request("https://example.test"), "workflow_runs", "total_count")


@pytest.mark.parametrize("status", ["waiting", "pending", "queued", "requested", "in_progress"])
def test_later_lock_waiter_does_not_abort_the_current_publication(status):
    """A successor waits behind this workflow's shared lock and cannot invalidate its gate."""
    from scripts.static_ops.external_trigger_gate import preceding_active
    later = {**run(101, -1, "20261025000000"), "status": status}
    validate(EVENT, ENV, [run(), later], NOW)
    assert not preceding_active([later], 100, NOW)
    assert preceding_active([{**later, "created_at": (NOW - timedelta(days=3)).isoformat()}],
                            100, NOW)


def test_unknown_active_metadata_is_never_treated_as_a_later_waiter():
    """Missing or unknown statuses cannot establish safe request ordering."""
    from scripts.static_ops.external_trigger_gate import preceding_active
    with pytest.raises(ValueError, match="Unknown"):
        preceding_active([{**run(101, -1), "status": "unexpected"}], 100, NOW)
    with pytest.raises(KeyError):
        preceding_active([{"id": 101, "created_at": NOW.isoformat()}], 100, NOW)
