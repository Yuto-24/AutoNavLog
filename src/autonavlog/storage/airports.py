from __future__ import annotations

import csv
from pathlib import Path

from autonavlog.domain.project import Airport


class AirportRepository:
    def __init__(self, airports: list[Airport]):
        self._airports = {airport.id: airport for airport in airports}
        if len(self._airports) != len(airports):
            raise ValueError("airport ids must be unique")

    @classmethod
    def from_csv(cls, path: str | Path) -> AirportRepository:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return cls([Airport.model_validate(row) for row in csv.DictReader(handle)])

    def get(self, airport_id: str) -> Airport:
        try:
            return self._airports[airport_id]
        except KeyError as error:
            raise KeyError(f"airport {airport_id!r} is not available") from error

    def all(self) -> list[Airport]:
        return sorted(self._airports.values(), key=lambda item: (item.icao, item.name))
