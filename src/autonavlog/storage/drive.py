from __future__ import annotations

from pathlib import Path

from .local import LocalProjectRepository


class GoogleDriveProjectRepository(LocalProjectRepository):
    """Repository for a mounted Colab Drive path using the same revision semantics."""

    def __init__(self, my_drive_root: str | Path):
        root = Path(my_drive_root)
        super().__init__(root / "AutoNavLog")
