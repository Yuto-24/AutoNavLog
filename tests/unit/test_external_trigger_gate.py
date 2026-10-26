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
@pytest.mark.parametrize("age", [-1, 120])
def test_confirmed_lock_waiter_does_not_abort_current_publication(status, age):
    """Live lock waiting, not creation order, distinguishes a harmless competitor."""
    from scripts.static_ops.external_trigger_gate import conflicting_active, lock_waiters
    other = {**run(101, age, "20261025000000"), "status": status}
    waiters = lock_waiters({"group_name": "static-production", "total_count": 2,
                            "group_members": [{"run_id": 100, "status": "in_progress"},
                                              {"run_id": 101, "status": "pending"}]}, 100)
    # Exempt only the active check. Actual external requests retain their cooldown.
    other["display_title"] = "static-production"
    validate(EVENT, ENV, [run(), other], NOW, waiters)
    assert not conflicting_active([other], 100, waiters)
    assert conflicting_active([other], 100, set())


@pytest.mark.parametrize("change", [
    {"group_name": "other"}, {"total_count": 0}, {"total_count": 101},
    {"total_count": True}, {"group_members": []},
    {"group_members": [{"run_id": 99, "status": "in_progress"}]},
    {"group_members": [{"run_id": 100, "status": "pending"}]},
    {"group_members": [{"run_id": 100, "status": "waiting"}]},
    {"group_members": [{"run_id": 100, "status": "in_progress", "job_id": 123}]},
    {"group_members": [{"run_id": "100", "status": "in_progress"}]},
    {"total_count": 2, "group_members": [{"run_id": 100, "status": "in_progress"}] * 2},
    {"total_count": 2, "group_members": [{"run_id": 100, "status": "in_progress"},
                                         {"run_id": 99, "status": "in_progress"}]},
])
def test_missing_ambiguous_or_unowned_lock_never_grants_admission(change):
    """Malformed, incomplete or non-owning live evidence must stop the gate."""
    from scripts.static_ops.external_trigger_gate import lock_waiters
    value = {"group_name": "static-production", "total_count": 1,
             "group_members": [{"run_id": 100, "status": "in_progress"}]}
    with pytest.raises(ValueError):
        lock_waiters({**value, **change}, 100)


def test_lock_waiter_still_consumes_request_budget_and_cooldown():
    """The active-state exemption does not exempt external request accounting."""
    with pytest.raises(ValueError, match="cooldown"):
        validate(EVENT, ENV, [run(), {**run(99, 30), "status": "pending"}], NOW, {99})
    with pytest.raises(ValueError, match="daily"):
        validate(EVENT, ENV, [run(), *[run(i, 120, "20261025000000") for i in range(8)]],
                 NOW, set(range(8)))


def test_unknown_active_metadata_is_never_treated_as_a_lock_waiter():
    """Unknown statuses cannot establish safe activity, even for a known waiter."""
    from scripts.static_ops.external_trigger_gate import conflicting_active
    with pytest.raises(ValueError, match="Unknown"):
        conflicting_active([{**run(101, -1), "status": "unexpected"}], 100, {101})
    with pytest.raises(KeyError):
        conflicting_active([{"id": 101, "created_at": NOW.isoformat()}], 100, {101})


@pytest.mark.parametrize("lock_state", ["owned", "missing", "foreign", "unavailable"])
def test_main_requires_live_lock_after_reading_active_history(monkeypatch, tmp_path, capsys,
                                                            lock_state):
    """The command checks the actual lease and keeps failures closed without new permissions."""
    import io
    import json

    from scripts.static_ops import external_trigger_gate as gate
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GH_TOKEN", "fixture-only")
    monkeypatch.setattr("sys.argv", ["external_trigger_gate.py"])

    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({"created_at": NOW.isoformat()}).encode())
    monkeypatch.setattr(gate, "build_opener", lambda *_: Opener())
    calls = []
    lock_reads = []
    # This older scheduled run is pending on our lock, not publishing in parallel.
    other = {**run(99, 120), "display_title": "static-production", "status": "pending"}
    def history(query):
        calls.append(query)
        if "created" in query:
            return [run(), other]
        return [other] if query["status"] == "pending" else []
    monkeypatch.setattr(gate, "history", history)
    def read_lock(request):
        # Record only: main() converts exceptions from this mock into SystemExit.
        lock_reads.append((len(calls), request.full_url))
        if lock_state == "unavailable":
            raise TimeoutError("private provider details")
        return {"group_name": "static-production", "total_count": 2,
                "group_members": [{"run_id": 100 if lock_state != "missing" else 101,
                                   "status": ("in_progress" if lock_state != "foreign"
                                              else "pending")},
                                  {"run_id": 99, "status": "pending"}]}
    monkeypatch.setattr(gate, "read_json", read_lock)
    if lock_state == "owned":
        gate.main()
        assert "EXTERNAL_BUDGET_OK" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit, match="External trigger gate blocked"):
            gate.main()
    # Keep assertions outside main() so its failure handling cannot swallow them.
    assert len(calls) == 6
    assert set(calls[0]) == {"created"}
    assert calls[1:] == [{"status": status} for status in
                         ("queued", "in_progress", "waiting", "pending", "requested")]
    assert lock_reads == [(6, "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/"
                          "concurrency_groups/static-production")]
