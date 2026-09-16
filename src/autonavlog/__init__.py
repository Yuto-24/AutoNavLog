"""NavMate public package API."""

from .domain.calculation import CalculationOutcome
from .domain.project import Project
from .version import __version__

__all__ = ["CalculationOutcome", "Project", "__version__"]
