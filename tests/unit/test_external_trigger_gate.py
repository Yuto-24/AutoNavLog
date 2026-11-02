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


SECRET = "secret-token-provider-body-DO-NOT-LOG"
BLOCK_PREFIX = "External trigger gate blocked; check inputs, Actions availability and limits"
API_STAGES = ["current_run", "created_history", "active_queued", "active_in_progress",
              "active_waiting", "active_pending", "active_requested", "live_concurrency",
              "detected_history", "detected_jobs"]


@pytest.fixture
def gate_command(monkeypatch, tmp_path):
    """Exercise real parsing and validation with only the clock and transport replaced."""
    import io
    import json
    from types import SimpleNamespace

    from scripts.static_ops import external_trigger_gate as gate

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT))
    for key, value in {**ENV, "GH_TOKEN": SECRET, "GITHUB_EVENT_PATH": str(event_path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(gate, "datetime", Clock)
    monkeypatch.setattr("sys.argv", ["gate"])
    lock = {"group_name": "static-production", "total_count": 1,
            "group_members": [{"run_id": 100, "status": "in_progress"}]}
    def history(rows):
        return {"total_count": len(rows), "workflow_runs": rows}
    state = SimpleNamespace(
        gate=gate, event_path=event_path, history=history, calls=[],
        responses=[{"created_at": NOW.isoformat()}, history([run()]),
                   *[history([]) for _ in range(5)], lock])

    class Opener:
        def open(self, request, timeout):
            value = state.responses[len(state.calls)]
            state.calls.append((request, timeout))
            if isinstance(value, Exception):
                raise value
            return io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())

    monkeypatch.setattr(gate, "build_opener", lambda *_: Opener())
    return state


def assert_blocked(state, capsys, stage, reason, http_status=None):
    """Assert the complete allowed output, not just absence of one known token."""
    with pytest.raises(SystemExit) as exc:
        state.gate.main()
    status = "" if http_status is None else f" http_status={http_status}"
    assert str(exc.value) == f"{BLOCK_PREFIX}; stage={stage} reason={reason}{status}"
    assert exc.value.__suppress_context__
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("detected", [False, True])
def test_main_success_preserves_output_requests_and_auth(gate_command, monkeypatch, capsys,
                                                       detected):
    """Success retains exact output, GET order/count, timeout and existing authentication."""
    from urllib.parse import parse_qs, urlsplit

    state = gate_command
    if detected:
        monkeypatch.setattr("sys.argv", ["gate", "--detected-run", TARGET])
        state.responses += [state.history([run(), {**run(99, 120),
                                                   "display_title": "msm-request/old"}]),
                            {"total_count": 1, "jobs": [{"id": 1, "name": "preflight"}]}]
    state.gate.main()
    assert capsys.readouterr().out == '{"status":"EXTERNAL_BUDGET_OK"}\n'
    urls = [urlsplit(request.full_url) for request, _ in state.calls]
    assert len(urls) == (10 if detected else 8)
    assert urls[0].path.endswith("/runs/100")
    assert parse_qs(urls[1].query) == {
        "per_page": ["100"], "created": [">=2026-10-24T12:00:00+00:00"]}
    assert [parse_qs(url.query) for url in urls[2:7]] == [
        {"per_page": ["100"], "status": [status]}
        for status in ("queued", "in_progress", "waiting", "pending", "requested")]
    assert urls[7].path.endswith("/concurrency_groups/static-production")
    if detected:
        assert parse_qs(urls[8].query) == parse_qs(urls[1].query)
        assert urls[9].path.endswith("/runs/99/jobs")
    assert all(request.get_method() == "GET" and timeout == 30
               and request.get_header("Authorization") == "Bearer " + SECRET
               for request, timeout in state.calls)


@pytest.mark.parametrize("stage_index", range(10))
@pytest.mark.parametrize("http", [False, True])
def test_main_transport_failures_stop_at_stage_without_secrets(gate_command, monkeypatch, capsys,
                                                             stage_index, http):
    """Every HTTP read stops immediately; exception text, URL and body never cross the boundary."""
    import io
    from urllib.error import HTTPError

    state = gate_command
    monkeypatch.setattr("sys.argv", ["gate", "--detected-run", TARGET])
    state.responses += [state.history([run(), {**run(99, 120),
                                               "display_title": "msm-request/old"}]),
                        {"total_count": 0, "jobs": []}]
    error = (HTTPError("https://example.test/" + SECRET, 403, SECRET,
                       {"Authorization": SECRET}, io.BytesIO(SECRET.encode()))
             if http else TimeoutError(SECRET))
    state.responses[stage_index] = error
    assert_blocked(state, capsys, API_STAGES[stage_index],
                   "HTTP_ERROR" if http else "UNEXPECTED_ERROR", 403 if http else None)
    assert len(state.calls) == stage_index + 1


@pytest.mark.parametrize("code, expected", [
    (100, 100), (401, 401), (429, 429), (500, 500), (599, 599),
    (99, None), (600, None), (True, None), ("403 " + SECRET, None), (403.0, None), (None, None),
])
def test_main_http_status_is_bounded_plain_integer(gate_command, capsys, code, expected):
    """Malformed status values cannot inject diagnostics or bypass a failed request."""
    from urllib.error import HTTPError

    state = gate_command
    state.responses[0] = HTTPError(SECRET, code, SECRET, {}, None)
    assert_blocked(state, capsys, "current_run", "HTTP_ERROR", expected)


@pytest.mark.parametrize("case,stage,reason", [
    ("inputs", "validate", "INVALID_INPUTS"),
    ("rerun", "validate", "INVALID_INPUTS"),
    ("observed", "validate", "INVALID_OBSERVED_RUN"),
    ("missing_current", "validate", "CURRENT_REQUEST_MISSING"),
    ("daily", "validate", "DAILY_LIMIT"),
    ("per_run", "validate", "PER_RUN_LIMIT"),
    ("duplicate", "validate", "DUPLICATE_REQUEST"),
    ("cooldown", "validate", "COOLDOWN"),
    ("history_active", "validate", "ACTIVE_CONFLICT"),
    ("active", "validate_active", "ACTIVE_CONFLICT"),
    ("unknown_status", "validate_active", "UNKNOWN_ACTIONS_STATUS"),
    ("lock", "validate_lock", "UNKNOWN_LOCK"),
    ("lease", "validate_lock", "UNKNOWN_LEASE"),
    ("not_held", "validate_lock", "LOCK_NOT_HELD"),
    ("lock_conflict", "validate_lock", "LOCK_CONFLICT"),
    ("missing_lock", "validate_lock", "LOCK_MISSING"),
    ("current_oversize", "current_run", "RESPONSE_TOO_LARGE"),
    ("history_oversize", "created_history", "RESPONSE_TOO_LARGE"),
    ("incomplete", "created_history", "INCOMPLETE_HISTORY"),
    ("detected_future", "detected_run", "FUTURE_DETECTED_RUN"),
    ("detected_invalid", "detected_limit", "INVALID_DETECTED_RUN"),
    ("detected_cap", "detected_limit", "PER_RUN_LIMIT"),
    ("jobs_incomplete", "detected_jobs", "INCOMPLETE_HISTORY"),
])
def test_main_rejections_have_distinct_safe_reasons(gate_command, monkeypatch, capsys,
                                                  case, stage, reason):
    """All explicit denial branches retain fail-closed behavior and bounded diagnostics."""
    import json

    state = gate_command
    rows = [run()]
    event = {"inputs": dict(EVENT["inputs"])}
    lock = state.responses[7]
    if case == "inputs":
        event["inputs"]["source_sha"] = SECRET
    elif case == "rerun":
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", SECRET)
    elif case == "observed":
        event["inputs"]["observed_run"] = "20261025150000"
    elif case == "missing_current":
        rows = []
    elif case == "daily":
        rows += [run(i, 120, "20261025000000") for i in range(8)]
    elif case == "per_run":
        rows += [run(98, 120), run(99, 240)]
    elif case == "duplicate":
        event["inputs"].pop("observed_run")
        event["inputs"]["request_id"] = "1792929600000"
        rows = [{**run(i), "display_title": "msm-request/1792929600000"} for i in (100, 99)]
    elif case in ("cooldown", "history_active"):
        rows += [{**run(99, 30), "status": "in_progress" if case == "history_active"
                  else "completed"}]
    elif case in ("active", "unknown_status"):
        state.responses[2] = state.history([{**run(99), "status":
                                             SECRET if case == "unknown_status" else "queued"}])
    elif case == "lock":
        lock["group_name"] = SECRET
    elif case == "lease":
        lock["group_members"][0]["run_id"] = SECRET
    elif case == "not_held":
        lock["group_members"][0]["status"] = "pending"
    elif case == "lock_conflict":
        lock["group_members"][0]["run_id"] = 99
    elif case == "missing_lock":
        lock["group_members"][0] = {"run_id": 99, "status": "pending"}
    state.event_path.write_text(json.dumps(event))
    state.responses[1] = state.history(rows)
    if case in ("current_oversize", "history_oversize"):
        state.responses[0 if case == "current_oversize" else 1] = (
            SECRET.encode() + b" " * (2 * 1024 * 1024))
    elif case == "incomplete":
        state.responses[1]["total_count"] = 100
    if case.startswith("detected_") or case == "jobs_incomplete":
        target = {"detected_future": "20261025150000",
                  "detected_invalid": "20261025070000"}.get(case, TARGET)
        monkeypatch.setattr("sys.argv", ["gate", "--detected-run", target])
        prior = [run(), {**run(99, 120), "display_title": "msm-request/" + SECRET}]
        if case == "detected_cap":
            prior += [run(98, 240)]
        state.responses += [state.history(prior),
                            {"total_count": 1 if case == "jobs_incomplete" else 0, "jobs": []}]
        if case == "detected_cap":
            state.responses[-1] = {"total_count": 1,
                                   "jobs": [{"id": 1, "name": "msm-detected/" + TARGET}]}
    assert_blocked(state, capsys, stage, reason)


@pytest.mark.parametrize("case,stage", [
    ("event_json", "event"), ("event_shape", "validate"), ("missing_env", "current_run"),
    ("current_json", "current_run"), ("current_date", "current_run"),
    ("history_json", "created_history"), ("history_shape", "created_history"),
    ("lock_shape", "validate_lock"), ("detected_parse", "detected_run"),
    ("jobs_json", "detected_jobs"),
])
def test_main_malformed_evidence_never_formats_payload(gate_command, monkeypatch, capsys, case,
                                                     stage):
    """Unexpected parser/type/key errors stay generic and never include offending data."""
    state = gate_command
    if case == "event_json":
        state.event_path.write_text(SECRET)
    elif case == "event_shape":
        state.event_path.write_text('{"inputs": "' + SECRET + '"}')
    elif case == "missing_env":
        monkeypatch.delenv("GH_TOKEN")
    elif case == "current_json":
        state.responses[0] = SECRET.encode()
    elif case == "current_date":
        state.responses[0] = {"created_at": SECRET}
    elif case == "history_json":
        state.responses[1] = SECRET.encode()
    elif case == "history_shape":
        state.responses[1] = {SECRET: SECRET}
    elif case == "lock_shape":
        state.responses[7]["group_members"] = [SECRET]
    elif case == "detected_parse":
        monkeypatch.setattr("sys.argv", ["gate", "--detected-run", SECRET])
    elif case == "jobs_json":
        monkeypatch.setattr("sys.argv", ["gate", "--detected-run", TARGET])
        state.responses += [state.history([run(), {**run(99, 120),
                                                   "display_title": "msm-request/old"}]),
                            SECRET.encode()]
    assert_blocked(state, capsys, stage, "UNEXPECTED_ERROR")
