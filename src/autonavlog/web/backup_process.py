"""Killable compute subprocess; no owner session or saved-project repository."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from autonavlog.forecast_backup import BackupRequest, BackupResult


@dataclass(frozen=True)
class ProcessCalculator:
    data_root: Path
    cache_root: Path
    deadline_seconds: float = 120

    def __call__(self, request: BackupRequest) -> BackupResult:
        # No request, capability, identity or token appears in process arguments/logs.
        for candidate in self.cache_root.glob("job-*"):
            try:
                if (
                    candidate.is_dir()
                    and not candidate.is_symlink()
                    and candidate.stat().st_mtime < time.time() - 600
                ):
                    shutil.rmtree(candidate)
            except FileNotFoundError:
                # Another worker can reclaim the same expired orphan concurrently.
                continue
        with TemporaryDirectory(prefix="job-", dir=self.cache_root) as directory:
            return self._compute(request, Path(directory))

    def _compute(self, request: BackupRequest, directory: Path) -> BackupResult:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "autonavlog.web.backup_runner",
                str(self.data_root.resolve()),
                str(directory.resolve()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(
                request.model_dump_json().encode(), timeout=self.deadline_seconds
            )
            if process.returncode != 0 or len(output) > 2 * 1024 * 1024:
                raise ValueError("backup calculation failed")
            return BackupResult.model_validate_json(output)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
