from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from autonavlog.domain.planning import (
    AirportSelection,
    PatternAltitudeValidationStatus,
)
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository


def write_reference_pack(
    target: Path,
    *,
    verified: bool = True,
    include_airports: bool = True,
) -> Path:
    """Write a strict reference pack for release/bundle tests."""

    target.parent.mkdir(parents=True, exist_ok=True)
    status = (
        PatternAltitudeValidationStatus.VERIFIED
        if verified
        else PatternAltitudeValidationStatus.UNVERIFIED
    )
    airports = (
        [
            AirportSelection(
                id="RJFM",
                icao="RJFM",
                name="Miyazaki",
                latitude_deg=31.877,
                longitude_deg=131.449,
                elevation_ft_msl=19.0,
                pattern_altitude_ft_msl=1000.0,
                pattern_altitude_source="CAC primary-source fixture",
                pattern_altitude_source_revision="CAC-REV19",
                pattern_altitude_validation_status=status,
                source="official airport fixture",
                source_revision="2026-01",
            ),
            AirportSelection(
                id="RJFO",
                icao="RJFO",
                name="Oita",
                latitude_deg=33.479,
                longitude_deg=131.737,
                elevation_ft_msl=17.0,
                pattern_altitude_ft_msl=1000.0,
                pattern_altitude_source="CAC primary-source fixture",
                pattern_altitude_source_revision="CAC-REV19",
                pattern_altitude_validation_status=status,
                source="official airport fixture",
                source_revision="2026-01",
            ),
        ]
        if include_airports
        else []
    )
    with tempfile.TemporaryDirectory(
        prefix=".reference-fixture-",
        dir=target.parent,
    ) as staging:
        catalog = ReferenceDataCatalogRepository(Path(staging)).publish_revision(
            dataset_id="release-fixture",
            revision="2026-01",
            airports=airports,
            points=[],
            check_points=[],
            activate=False,
        )
        shutil.copytree(catalog.root, target)
    return target
