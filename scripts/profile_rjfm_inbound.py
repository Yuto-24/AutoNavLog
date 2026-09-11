#!/usr/bin/env python3
"""Measure phases of the five real inbound regressions, including HTTP workers.

Run separately from timing/coverage measurements: instrumentation adds overhead.
No solver input, output, search budget, or test assertion is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from threading import get_ident, local
from time import perf_counter

import pytest

UNIT = "tests/unit/test_rjfm_inbound_guidance.py::"
TARGETS = [
    UNIT + "test_solver_finds_non_grid_calm_solution_and_beats_coarse_scan",
    UNIT + "test_solver_handles_forecast_wind_with_feasible_rounded_solution",
    UNIT + "test_solver_can_select_bearing_range_boundaries",
    "tests/integration/test_rjfm_inbound_web.py::"
    "test_production_available_reference_returns_numeric_v2_guidance",
]


class SolverProfile:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.current_test = ""
        self.calls: list[dict] = []
        self.test_calls: dict[str, float] = {}
        self.patch = pytest.MonkeyPatch()
        self.local = local()
        self.configured = False

    def measure_phase(self, module, name: str) -> None:
        original = getattr(module, name)

        @wraps(original)
        def measured(*args, **kwargs):
            phases = getattr(self.local, "phases", None)
            if phases is None:
                return original(*args, **kwargs)
            if name == "_evaluate_distance":
                self.local.trace.update(repr((args[1:], kwargs)).encode())
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                phase = phases.setdefault(name, dict(calls=0, seconds=0.0))
                phase["calls"] += 1
                phase["seconds"] += perf_counter() - started

        self.patch.setattr(module, name, measured)

    def pytest_configure(self, config) -> None:
        import autonavlog.application.rjfm_inbound_geometry as geometry
        import autonavlog.application.rjfm_inbound_guidance as guidance
        import autonavlog.application.rjfm_inbound_service as service

        if self.configured:
            raise RuntimeError("solver instrumentation must only be configured once")
        self.configured = True
        original = guidance.solve_rjfm_inbound_west_extension
        for name in (
            "_search_bearing",
            "_evaluate_distance",
            "_route_metrics",
            "_rounded_turn_point",
            "_leg",
            "solve_wind_triangle",
        ):
            self.measure_phase(guidance, name)
        for name in (
            "_route_distances",
            "_segment_clearance_nm",
            "sample_geodesic_points",
            "_prepare_polygon",
        ):
            self.measure_phase(geometry, name)

        def measured(request):
            # Scope counters/timers to the actual calling thread, not pytest's thread.
            if getattr(self.local, "phases", None) is not None:
                raise RuntimeError("unexpected nested solver instrumentation")
            phases: dict = {}
            self.local.phases = phases
            self.local.trace = hashlib.sha256()
            started = perf_counter()
            try:
                result = original(request)
            finally:
                elapsed = perf_counter() - started
                self.local.phases = None
            self.calls.append(
                dict(
                    test=self.current_test,
                    thread=get_ident(),
                    seconds=elapsed,
                    phases=phases,
                    evaluation_trace=self.local.trace.hexdigest(),
                    request=asdict(request),
                    result=asdict(result),
                )
            )
            return result

        self.patch.setattr(guidance, "solve_rjfm_inbound_west_extension", measured)
        self.patch.setattr(service, "solve_rjfm_inbound_west_extension", measured)
        # The service captures its callable default at definition time.
        self.patch.setitem(service.build_rjfm_inbound_guidance.__kwdefaults__, "solver", measured)

    def pytest_runtest_setup(self, item) -> None:
        self.current_test = item.nodeid

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call":
            self.test_calls[report.nodeid] = report.duration

    def pytest_unconfigure(self, config) -> None:
        self.patch.undo()
        (self.output / "solutions.json").write_text(
            json.dumps(self.calls, indent=2) + "\n", encoding="utf-8"
        )
        (self.output / "test-times.json").write_text(
            json.dumps(self.test_calls, indent=2) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    return int(pytest.main([*TARGETS, "--durations=10"], plugins=[SolverProfile(args.output)]))


if __name__ == "__main__":
    raise SystemExit(main())
