"""Regression evidence from delayed Cron delivery and rounded tail CPU readings."""

from copy import deepcopy

import pytest

from scripts.static_ops.msm_observation import assess_cpu, correlate_dispatch, cpu_window

VERSION = "deployed-version"
COMMIT = "a" * 40
REQUEST = "1793106124227"
RUN_ID = 40508561293
RECEIPT = {
    "status": "DISPATCH_ACCEPTED",
    "request_id": REQUEST,
    "workflow_run_id": RUN_ID,
    "run_url": f"https://api.github.com/repos/Yuto-24/AutoNavLog/actions/runs/{RUN_ID}",
    "html_url": f"https://github.com/Yuto-24/AutoNavLog/actions/runs/{RUN_ID}",
    "publication": "NOT_VERIFIED",
}
TICK = {
    "version_id": VERSION,
    "cron": "*/15 * * * *",
    "outcome": "ok",
    "cpuTime": 10,
    "eventTimestamp": 1793106030154,
    "scheduled_time": 1793106026535,
    "wallTime": 8815,
    "results": [RECEIPT],
    "diagnostics": [{"stage": "dispatch", "status": 200}],
}
RUN = {
    "id": RUN_ID,
    "display_title": "msm-request/" + REQUEST,
    "event": "workflow_dispatch",
    "head_sha": COMMIT,
    "run_attempt": 1,
    "status": "waiting",
}


def provider(cpu=9000, requests=1):
    return {
        "data": {
            "viewer": {
                "accounts": [
                    {
                        "workersInvocationsAdaptive": [
                            {"sum": {"requests": requests}, "max": {"cpuTime": cpu}}
                        ]
                    }
                ]
            }
        },
        "errors": None,
    }


def test_delayed_natural_cron_matches_returned_run_without_a_timestamp_distance_gate():
    result = correlate_dispatch(TICK, RUN, VERSION, COMMIT)
    assert result["workflow_run_id"] == RUN_ID
    assert result["request_id"] == REQUEST
    assert int(REQUEST) - TICK["eventTimestamp"] > 90_000
    start, end = cpu_window(TICK, result)
    assert start == "2026-10-27T13:00:21.535000Z"
    assert end == "2026-10-27T13:03:13.042000Z"
    assert "publication" not in result


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", RUN_ID + 1),
        ("display_title", "msm-request/1793100615588"),
        ("event", "schedule"),
        ("head_sha", "b" * 40),
        ("run_attempt", 2),
    ],
)
def test_another_run_or_rerun_is_never_accepted(field, value):
    run = {**RUN, field: value}
    with pytest.raises(ValueError, match="does not match"):
        correlate_dispatch(TICK, run, VERSION, COMMIT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_run_id", True),
        ("request_id", "private"),
        ("html_url", "https://private.invalid"),
        ("publication", "VERIFIED"),
    ],
)
def test_invalid_receipts_fail_without_echoing_the_response(field, value):
    tick = deepcopy(TICK)
    tick["results"][0][field] = value
    with pytest.raises(ValueError, match="^Invalid dispatch receipt$"):
        correlate_dispatch(tick, RUN, VERSION, COMMIT)


def test_wrong_deployment_and_missing_dispatch_are_unknown():
    with pytest.raises(ValueError):
        correlate_dispatch(TICK, RUN, "other", COMMIT)
    with pytest.raises(ValueError):
        correlate_dispatch({**TICK, "results": []}, RUN, VERSION, COMMIT)


@pytest.mark.parametrize(
    "cpu,expected",
    [
        (8952, "WITHIN_LIMIT"),
        (10_000, "WITHIN_LIMIT"),
        (10_001, "OVER_LIMIT"),
        (10_913, "OVER_LIMIT"),
    ],
)
def test_provider_precision_controls_the_cpu_gate(cpu, expected):
    assert assess_cpu(TICK, provider(cpu))["status"] == expected


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"errors": [{"message": "budget depleted"}]},
        {},
        provider(requests=0),
        provider(requests=2),
        provider(cpu=0),
        provider(cpu=float("nan")),
        provider(cpu=-1),
        provider(cpu=True),
    ],
)
def test_observation_unknown_and_ambiguous_samples_never_pass(response):
    assert assess_cpu(TICK, response) == {"status": "UNKNOWN"}


def test_execution_failure_and_known_tail_excess_are_not_hidden_by_api_unknown():
    assert assess_cpu({**TICK, "outcome": "exception"}, {"errors": [{}]}) == {
        "status": "WORKER_FAILED"
    }
    assert assess_cpu({**TICK, "cpuTime": 11}, {"errors": [{}]})["status"] == "OVER_LIMIT"


@pytest.mark.parametrize("outcome", [None, "unknown", True])
def test_missing_execution_observation_is_not_reported_as_worker_failure(outcome):
    assert assess_cpu({**TICK, "outcome": outcome}, provider()) == {"status": "UNKNOWN"}


@pytest.mark.parametrize(
    "key,value",
    [
        ("scheduled_time", None),
        ("eventTimestamp", True),
        ("wallTime", -1),
        ("wallTime", float("inf")),
    ],
)
def test_missing_or_invalid_timestamps_cannot_form_a_provider_window(key, value):
    with pytest.raises(ValueError):
        cpu_window({**TICK, key: value}, RECEIPT)
