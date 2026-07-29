from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from math import hypot
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree


class KmlImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportLimits:
    max_archive_bytes: int = 10 * 1024 * 1024
    max_expanded_bytes: int = 50 * 1024 * 1024
    max_files: int = 50
    max_coordinates: int = 50_000
    max_display_vertices: int = 2_000


@dataclass(frozen=True)
class ImportedPoint:
    name: str
    latitude_deg: float
    longitude_deg: float
    altitude_m: float | None = None


@dataclass(frozen=True)
class ImportedLine:
    name: str
    coordinates: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class KmlImportResult:
    points: tuple[ImportedPoint, ...] = ()
    lines: tuple[ImportedLine, ...] = ()
    warnings: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _coordinates(text: str | None) -> list[tuple[float, float, float | None]]:
    output = []
    for item in (text or "").split():
        parts = item.split(",")
        if len(parts) < 2:
            raise KmlImportError("invalid KML coordinate")
        longitude, latitude = float(parts[0]), float(parts[1])
        altitude = float(parts[2]) if len(parts) > 2 and parts[2] else None
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise KmlImportError("KML coordinate is outside latitude/longitude bounds")
        output.append((latitude, longitude, altitude))
    return output


def _point_line_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    if start == end:
        return hypot(point[0] - start[0], point[1] - start[1])
    dx, dy = end[0] - start[0], end[1] - start[1]
    fraction = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    fraction = max(0.0, min(1.0, fraction))
    projected = (start[0] + fraction * dx, start[1] + fraction * dy)
    return hypot(point[0] - projected[0], point[1] - projected[1])


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    distances = [
        _point_line_distance(point, points[0], points[-1])
        for point in points[1:-1]
    ]
    maximum = max(distances, default=0.0)
    if maximum <= tolerance:
        return [points[0], points[-1]]
    index = distances.index(maximum) + 1
    return _rdp(points[: index + 1], tolerance)[:-1] + _rdp(points[index:], tolerance)


def _simplify(points: list[tuple[float, float]], maximum: int) -> tuple[tuple[float, float], ...]:
    if len(points) <= maximum:
        return tuple(points)
    tolerance = 1e-6
    simplified = points
    while len(simplified) > maximum:
        simplified = _rdp(points, tolerance)
        tolerance *= 2
    return tuple(simplified)


def _parse_kml(
    data: bytes,
    limits: ImportLimits,
) -> tuple[list[ImportedPoint], list[ImportedLine], int]:
    root = ElementTree.fromstring(data)
    points: list[ImportedPoint] = []
    lines: list[ImportedLine] = []
    coordinate_count = 0
    for placemark in (item for item in root.iter() if _local_name(item.tag) == "Placemark"):
        name_element = next(
            (child for child in placemark.iter() if _local_name(child.tag) == "name"),
            None,
        )
        name = (name_element.text or "Unnamed").strip() if name_element is not None else "Unnamed"
        for geometry in placemark.iter():
            kind = _local_name(geometry.tag)
            if kind not in {"Point", "LineString"}:
                continue
            coordinates_element = next(
                (child for child in geometry.iter() if _local_name(child.tag) == "coordinates"),
                None,
            )
            parsed = _coordinates(
                None if coordinates_element is None else coordinates_element.text
            )
            coordinate_count += len(parsed)
            if coordinate_count > limits.max_coordinates:
                raise KmlImportError("KML coordinate limit exceeded")
            if kind == "Point" and parsed:
                latitude, longitude, altitude = parsed[0]
                points.append(ImportedPoint(name, latitude, longitude, altitude))
            elif len(parsed) >= 2:
                display = _simplify(
                    [(latitude, longitude) for latitude, longitude, _ in parsed],
                    limits.max_display_vertices,
                )
                lines.append(ImportedLine(name, display))
    return points, lines, coordinate_count


def _safe_kml_members(archive: ZipFile, limits: ImportLimits) -> Iterable[tuple[str, bytes]]:
    members = archive.infolist()
    if len(members) > limits.max_files:
        raise KmlImportError("KMZ file count limit exceeded")
    if sum(member.file_size for member in members) > limits.max_expanded_bytes:
        raise KmlImportError("KMZ expanded size limit exceeded")
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise KmlImportError("KMZ contains an unsafe path")
        if member.is_dir() or path.suffix.lower() != ".kml":
            continue
        yield member.filename, archive.read(member)


def import_kml_or_kmz(
    source: str | Path | bytes,
    *,
    filename: str | None = None,
    limits: ImportLimits | None = None,
) -> KmlImportResult:
    limits = limits or ImportLimits()
    if isinstance(source, bytes):
        data = source
        display_name = filename or "upload.kml"
    else:
        path = Path(source)
        data = path.read_bytes()
        display_name = filename or path.name
    if len(data) > limits.max_archive_bytes:
        raise KmlImportError("KML/KMZ archive size limit exceeded")

    documents: list[tuple[str, bytes]]
    if display_name.lower().endswith(".kmz") or data[:4] == b"PK\x03\x04":
        try:
            with ZipFile(BytesIO(data)) as archive:
                documents = list(_safe_kml_members(archive, limits))
        except BadZipFile as error:
            raise KmlImportError("invalid KMZ archive") from error
        if not documents:
            raise KmlImportError("KMZ does not contain a KML document")
    else:
        documents = [(display_name, data)]

    points: list[ImportedPoint] = []
    lines: list[ImportedLine] = []
    total_coordinates = 0
    for _, document in documents:
        parsed_points, parsed_lines, count = _parse_kml(document, limits)
        total_coordinates += count
        if total_coordinates > limits.max_coordinates:
            raise KmlImportError("KML coordinate limit exceeded")
        points.extend(parsed_points)
        lines.extend(parsed_lines)
    return KmlImportResult(
        points=tuple(points),
        lines=tuple(lines),
        source_files=tuple(name for name, _ in documents),
    )
