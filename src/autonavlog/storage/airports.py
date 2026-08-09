from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from autonavlog.domain.project import Airport

if TYPE_CHECKING:
    from autonavlog.storage.reference_data import ReferenceCatalog


class AirportRepository:
    def __init__(self, airports: list[Airport]):
        self._airports = {airport.id: airport for airport in airports}
        if len(self._airports) != len(airports):
            raise ValueError("airport ids must be unique")

    @classmethod
    def from_csv(cls, path: str | Path) -> AirportRepository:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return cls([Airport.model_validate(row) for row in csv.DictReader(handle)])

    @classmethod
    def from_reference_catalog(cls, catalog: ReferenceCatalog) -> AirportRepository:
        return cls(
            [
                Airport(
                    id=row.id,
                    icao=row.icao,
                    name=row.name,
                    latitude_deg=row.latitude_deg,
                    longitude_deg=row.longitude_deg,
                    elevation_ft_msl=row.elevation_ft_msl,
                    pattern_altitude_ft_msl=row.pattern_altitude_ft_msl,
                    source=row.source,
                    source_revision=row.source_revision,
                )
                for row in catalog.airports.values()
            ]
        )

    def get(self, airport_id: str) -> Airport:
        try:
            return self._airports[airport_id]
        except KeyError as error:
            raise KeyError(f"airport {airport_id!r} is not available") from error

    def all(self) -> list[Airport]:
        return sorted(self._airports.values(), key=lambda item: (item.icao, item.name))
