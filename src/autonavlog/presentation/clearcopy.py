from __future__ import annotations

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.project import Project

from .transfer_aid import render_transfer_aid_html


def render_clearcopy_html(project: Project, outcome: CalculationOutcome) -> str:
    """Backward-compatible entry point for the unofficial transcription aid."""

    return render_transfer_aid_html(project, outcome)
