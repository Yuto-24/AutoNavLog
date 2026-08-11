from __future__ import annotations

import re
import stat
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from math import hypot, isfinite
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element, ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from autonavlog.nav.geodesy import geodesic_leg

_ROUTE_NAME_SEPARATOR = re.compile(r"\s*[～〜~→⇒]\s*")
_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024


class KmlImportError(ValueError):
    pass


class KmlDocumentSelectionRequired(KmlImportError):
    def __init__(self, candidates: tuple[str, ...]):
        super().__init__("KMZ contains multiple KML documents; select one")
        self.candidates = candidates


@dataclass(frozen=True)
class ImportLimits:
    max_archive_bytes: int = 10 * 1024 * 1024
    max_expanded_bytes: int = 50 * 1024 * 1024
    max_files: int = 50
    max_coordinates: int = 50_000
    max_display_vertices: int = 2_000
    max_coordinates_in_selected_line: int = 500
    max_coordinates_in_selected_polygon_outer: int = 500

    def __post_init__(self) -> None:
        if self.max_archive_bytes < 1:
            raise ValueError("max_archive_bytes must be positive")
        if self.max_expanded_bytes < 1:
            raise ValueError("max_expanded_bytes must be positive")
        if self.max_files < 1:
            raise ValueError("max_files must be positive")
        if self.max_coordinates < 1:
            raise ValueError("max_coordinates must be positive")
        if self.max_display_vertices < 4:
            raise ValueError("max_display_vertices must be at least 4")
        if self.max_coordinates_in_selected_line < 2:
            raise ValueError("selected LineString limit must be at least 2")
        if self.max_coordinates_in_selected_polygon_outer < 4:
            raise ValueError("selected Polygon outer boundary limit must be at least 4")


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
    display_coordinates: tuple[tuple[float, float], ...] = ()
    original_coordinate_count: int = 0

    def __post_init__(self) -> None:
        if not self.display_coordinates:
            object.__setattr__(self, "display_coordinates", self.coordinates)
        if not self.original_coordinate_count:
            object.__setattr__(self, "original_coordinate_count", len(self.coordinates))


@dataclass(frozen=True)
class ImportedPolygon:
    """A horizontal KML polygon footprint, expressed as closed latitude/longitude rings."""

    name: str
    outer_boundary: tuple[tuple[float, float], ...]
    inner_boundaries: tuple[tuple[tuple[float, float], ...], ...] = ()
    minimum_altitude_m: float | None = None
    maximum_altitude_m: float | None = None
    altitude_mode: str | None = None
    display_outer_boundary: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.display_outer_boundary:
            object.__setattr__(self, "display_outer_boundary", self.outer_boundary)


@dataclass(frozen=True)
class KmlImportResult:
    points: tuple[ImportedPoint, ...] = ()
    lines: tuple[ImportedLine, ...] = ()
    polygons: tuple[ImportedPolygon, ...] = ()
    warnings: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _coordinates(text: str | None) -> list[tuple[float, float, float | None]]:
    output: list[tuple[float, float, float | None]] = []
    for item in (text or "").split():
        parts = item.split(",")
        if len(parts) not in {2, 3}:
            raise KmlImportError("invalid KML coordinate")
        try:
            longitude, latitude = float(parts[0]), float(parts[1])
            altitude = float(parts[2]) if len(parts) == 3 and parts[2] else None
        except (OverflowError, ValueError) as error:
            raise KmlImportError("invalid KML coordinate") from error
        if not isfinite(longitude) or not isfinite(latitude):
            raise KmlImportError("invalid KML coordinate")
        if altitude is not None and not isfinite(altitude):
            raise KmlImportError("invalid KML coordinate altitude")
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
    keep = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        if end_index <= start_index + 1:
            continue
        distances = [
            _point_line_distance(points[index], points[start_index], points[end_index])
            for index in range(start_index + 1, end_index)
        ]
        maximum = max(distances, default=0.0)
        if maximum <= tolerance:
            continue
        index = distances.index(maximum) + start_index + 1
        keep.add(index)
        pending.extend(((start_index, index), (index, end_index)))
    return [point for index, point in enumerate(points) if index in keep]


def _simplify(points: list[tuple[float, float]], maximum: int) -> tuple[tuple[float, float], ...]:
    if len(points) <= maximum:
        return tuple(points)
    tolerance = 1e-6
    simplified = points
    while len(simplified) > maximum:
        simplified = _rdp(points, tolerance)
        tolerance *= 2
    return tuple(simplified)


def named_waypoints_from_line(line: ImportedLine) -> tuple[ImportedPoint, ...]:
    """Map ordered names to unambiguous LineString coordinates.

    A full list maps one-to-one. A shorter list maps from the first intermediate
    coordinate, leaving the endpoints and any trailing operational points
    unnamed. Names are not adopted after coordinate deduplication because that
    can shift a valid label onto the wrong location.
    """

    names = tuple(part.strip() for part in _ROUTE_NAME_SEPARATOR.split(line.name) if part.strip())
    if len(names) < 2 or line.original_coordinate_count != len(line.coordinates):
        return ()
    if len(names) == len(line.coordinates):
        offset = 0
    elif len(names) <= len(line.coordinates) - 2:
        offset = 1
    else:
        return ()
    return tuple(
        ImportedPoint(
            name=name,
            latitude_deg=latitude,
            longitude_deg=longitude,
        )
        for name, (latitude, longitude) in zip(
            names,
            line.coordinates[offset : offset + len(names)],
            strict=True,
        )
    )


def _find_descendant(element: Element, local_name: str) -> Element | None:
    return next(
        (child for child in element.iter() if _local_name(child.tag) == local_name),
        None,
    )


def _horizontal_ring(
    parsed: list[tuple[float, float, float | None]],
) -> tuple[tuple[float, float], ...] | None:
    coordinates: list[tuple[float, float]] = []
    for latitude, longitude, _ in parsed:
        coordinate = (latitude, longitude)
        if not coordinates or coordinates[-1] != coordinate:
            coordinates.append(coordinate)
    if len(coordinates) > 1 and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    if len(set(coordinates)) < 3:
        return None
    coordinates.append(coordinates[0])
    return tuple(coordinates)


def _display_ring(
    ring: tuple[tuple[float, float], ...],
    maximum: int,
) -> tuple[tuple[float, float], ...]:
    if len(ring) <= maximum:
        return ring
    open_ring = list(ring[:-1])
    simplified = list(_simplify(open_ring, maximum - 1))
    if len(set(simplified)) < 3:
        simplified = [
            open_ring[0],
            open_ring[len(open_ring) // 2],
            open_ring[-1],
        ]
    simplified.append(simplified[0])
    return tuple(simplified)


def _polygon_boundaries(
    polygon: Element,
    limits: ImportLimits,
) -> tuple[
    tuple[tuple[float, float], ...] | None,
    tuple[tuple[tuple[float, float], ...], ...],
    int,
    float | None,
    float | None,
]:
    outer_boundary: tuple[tuple[float, float], ...] | None = None
    inner_boundaries: list[tuple[tuple[float, float], ...]] = []
    coordinate_count = 0
    altitudes: list[float] = []
    for boundary in polygon:
        kind = _local_name(boundary.tag)
        if kind not in {"outerBoundaryIs", "innerBoundaryIs"}:
            continue
        coordinates_element = _find_descendant(boundary, "coordinates")
        parsed = _coordinates(None if coordinates_element is None else coordinates_element.text)
        coordinate_count += len(parsed)
        if coordinate_count > limits.max_coordinates:
            raise KmlImportError("KML coordinate limit exceeded")
        altitudes.extend(altitude for _, _, altitude in parsed if altitude is not None)
        ring = _horizontal_ring(parsed)
        if kind == "outerBoundaryIs":
            if outer_boundary is None:
                outer_boundary = ring
        elif ring is not None:
            inner_boundaries.append(ring)
    return (
        outer_boundary,
        tuple(inner_boundaries),
        coordinate_count,
        min(altitudes, default=None),
        max(altitudes, default=None),
    )


def _parse_kml(
    data: bytes,
    limits: ImportLimits,
) -> tuple[
    list[ImportedPoint],
    list[ImportedLine],
    list[ImportedPolygon],
    list[str],
    int,
]:
    try:
        if b"<!doctype" in data.lower():
            raise KmlImportError("KML DOCTYPE declarations are not allowed")
        root = ElementTree.fromstring(
            data,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ParseError) as error:
        raise KmlImportError("invalid or unsafe KML document") from error
    if _local_name(root.tag) != "kml":
        raise KmlImportError("XML document is not KML")
    points: list[ImportedPoint] = []
    lines: list[ImportedLine] = []
    polygons: list[ImportedPolygon] = []
    warnings: list[str] = []
    coordinate_count = 0
    for placemark in (item for item in root.iter() if _local_name(item.tag) == "Placemark"):
        name_element = next(
            (child for child in placemark.iter() if _local_name(child.tag) == "name"),
            None,
        )
        name = (name_element.text or "Unnamed").strip() if name_element is not None else "Unnamed"
        skipped_polygon_surfaces = 0
        for geometry in placemark.iter():
            kind = _local_name(geometry.tag)
            if kind not in {"Point", "LineString", "Polygon"}:
                continue
            if kind == "Polygon":
                (
                    outer_boundary,
                    inner_boundaries,
                    count,
                    minimum_altitude_m,
                    maximum_altitude_m,
                ) = _polygon_boundaries(geometry, limits)
                coordinate_count += count
                if coordinate_count > limits.max_coordinates:
                    raise KmlImportError("KML coordinate limit exceeded")
                if outer_boundary is None:
                    skipped_polygon_surfaces += 1
                    continue
                altitude_mode_element = next(
                    (child for child in geometry if _local_name(child.tag) == "altitudeMode"),
                    None,
                )
                altitude_mode = (
                    (altitude_mode_element.text or "").strip() or None
                    if altitude_mode_element is not None
                    else None
                )
                polygons.append(
                    ImportedPolygon(
                        name=name,
                        outer_boundary=outer_boundary,
                        inner_boundaries=inner_boundaries,
                        minimum_altitude_m=minimum_altitude_m,
                        maximum_altitude_m=maximum_altitude_m,
                        altitude_mode=altitude_mode,
                        display_outer_boundary=_display_ring(
                            outer_boundary, limits.max_display_vertices
                        ),
                    )
                )
                continue
            coordinates_element = next(
                (child for child in geometry.iter() if _local_name(child.tag) == "coordinates"),
                None,
            )
            parsed = _coordinates(None if coordinates_element is None else coordinates_element.text)
            coordinate_count += len(parsed)
            if coordinate_count > limits.max_coordinates:
                raise KmlImportError("KML coordinate limit exceeded")
            if kind == "Point" and parsed:
                latitude, longitude, altitude = parsed[0]
                points.append(ImportedPoint(name, latitude, longitude, altitude))
            elif len(parsed) >= 2:
                coordinates = tuple((latitude, longitude) for latitude, longitude, _ in parsed)
                display = _simplify(
                    list(coordinates),
                    limits.max_display_vertices,
                )
                lines.append(ImportedLine(name, coordinates, display))
        if skipped_polygon_surfaces:
            warnings.append(
                f"{name}: skipped {skipped_polygon_surfaces} Polygon surface(s) "
                "without a usable horizontal boundary"
            )
    return points, lines, polygons, warnings, coordinate_count


def _safe_archive_path(filename: str) -> PurePosixPath:
    normalized = unicodedata.normalize("NFC", filename)
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\\" in filename
        or "\x00" in filename
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise KmlImportError("KMZ contains an unsafe path")
    return path


def _safe_kml_members(
    archive: ZipFile,
    limits: ImportLimits,
) -> list[tuple[str, bytes]]:
    members = archive.infolist()
    if len(members) > limits.max_files:
        raise KmlImportError("KMZ file count limit exceeded")
    seen_normalized: set[str] = set()
    seen_casefolded: set[str] = set()
    kml_members: list[tuple[str, bytes]] = []
    expanded_bytes = 0
    for member in members:
        path = _safe_archive_path(member.filename)
        normalized = path.as_posix()
        casefolded = normalized.casefold()
        if normalized in seen_normalized or casefolded in seen_casefolded:
            raise KmlImportError("KMZ contains duplicate normalized entries")
        seen_normalized.add(normalized)
        seen_casefolded.add(casefolded)
        if member.flag_bits & 0x1:
            raise KmlImportError("KMZ contains an encrypted entry")
        mode = (member.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise KmlImportError("KMZ contains a symbolic link")
        if member.is_dir():
            continue
        suffix = path.suffix.casefold()
        if suffix in {".zip", ".kmz"}:
            raise KmlImportError("KMZ contains a nested archive")
        chunks: list[bytes] = []
        signature = b""
        with archive.open(member) as handle:
            while True:
                remaining = limits.max_expanded_bytes - expanded_bytes
                chunk = handle.read(min(_ARCHIVE_READ_CHUNK_BYTES, remaining + 1))
                if not chunk:
                    break
                expanded_bytes += len(chunk)
                if expanded_bytes > limits.max_expanded_bytes:
                    raise KmlImportError("KMZ expanded size limit exceeded")
                if len(signature) < 4:
                    signature = (signature + chunk)[:4]
                if suffix == ".kml":
                    chunks.append(chunk)
        if signature == b"PK\x03\x04":
            raise KmlImportError("KMZ contains a nested archive")
        if suffix == ".kml":
            kml_members.append((member.filename, b"".join(chunks)))
    return kml_members


def _select_kmz_document(
    documents: list[tuple[str, bytes]],
    selected_filename: str | None,
) -> list[tuple[str, bytes]]:
    if not documents:
        raise KmlImportError("KMZ does not contain a KML document")
    doc_kml = [
        document
        for document in documents
        if PurePosixPath(document[0]).name.casefold() == "doc.kml"
    ]
    if len(doc_kml) > 1:
        raise KmlImportError("KMZ contains multiple doc.kml entries")
    if doc_kml:
        if selected_filename is not None and selected_filename != doc_kml[0][0]:
            raise KmlImportError("KMZ document selection does not match doc.kml")
        return doc_kml
    if len(documents) == 1:
        if selected_filename is not None and selected_filename != documents[0][0]:
            raise KmlImportError("selected KML document is unavailable")
        return documents
    if selected_filename is None:
        raise KmlDocumentSelectionRequired(tuple(name for name, _ in documents))
    selected = [document for document in documents if document[0] == selected_filename]
    if len(selected) != 1:
        raise KmlImportError("selected KML document is unavailable")
    return selected


def imported_line_length_nm(line: ImportedLine) -> float:
    return sum(
        geodesic_leg(start[0], start[1], end[0], end[1]).distance_nm
        for start, end in zip(
            line.coordinates,
            line.coordinates[1:],
            strict=False,
        )
    )


def _deduplicate_selected_line(
    coordinates: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    adopted: list[tuple[float, float]] = []
    final_index = len(coordinates) - 1
    for index, coordinate in enumerate(coordinates):
        if not adopted:
            adopted.append(coordinate)
            continue
        if index == final_index and coordinate == coordinates[0] and len(adopted) > 1:
            adopted.append(coordinate)
            continue
        separation_m = (
            geodesic_leg(
                adopted[-1][0],
                adopted[-1][1],
                coordinate[0],
                coordinate[1],
            ).distance_nm
            * 1852.0
        )
        if separation_m <= 10.0:
            continue
        adopted.append(coordinate)
    if len(adopted) < 2 or len(set(adopted)) < 2:
        raise KmlImportError("selected LineString has fewer than two points after deduplication")
    return tuple(adopted)


def select_imported_line(
    result: KmlImportResult,
    index: int,
    *,
    limits: ImportLimits | None = None,
) -> ImportedLine:
    limits = limits or ImportLimits()
    try:
        line = result.lines[index]
    except IndexError as error:
        raise KmlImportError("selected LineString is unavailable") from error
    if len(line.coordinates) > limits.max_coordinates_in_selected_line:
        raise KmlImportError("selected LineString coordinate limit exceeded")
    coordinates = _deduplicate_selected_line(line.coordinates)
    return ImportedLine(
        name=line.name,
        coordinates=coordinates,
        display_coordinates=_simplify(
            list(coordinates),
            limits.max_display_vertices,
        ),
        original_coordinate_count=line.original_coordinate_count,
    )


def select_imported_polygon_outer(
    result: KmlImportResult,
    index: int,
    *,
    limits: ImportLimits | None = None,
) -> tuple[tuple[float, float], ...]:
    limits = limits or ImportLimits()
    try:
        outer = result.polygons[index].outer_boundary
    except IndexError as error:
        raise KmlImportError("selected Polygon is unavailable") from error
    if len(outer) > limits.max_coordinates_in_selected_polygon_outer:
        raise KmlImportError("selected Polygon outer coordinate limit exceeded")
    return outer


def import_kml_or_kmz(
    source: str | Path | bytes,
    *,
    filename: str | None = None,
    limits: ImportLimits | None = None,
    kmz_kml_filename: str | None = None,
) -> KmlImportResult:
    limits = limits or ImportLimits()
    if isinstance(source, bytes):
        data = source
        display_name = filename or "upload.kml"
    else:
        path = Path(source)
        try:
            source_size = path.stat().st_size
        except OSError as error:
            raise KmlImportError(f"cannot inspect KML/KMZ source: {path}") from error
        if source_size > limits.max_archive_bytes:
            raise KmlImportError("KML/KMZ archive size limit exceeded")
        try:
            data = path.read_bytes()
        except OSError as error:
            raise KmlImportError(f"cannot read KML/KMZ source: {path}") from error
        display_name = filename or path.name
    if len(data) > limits.max_archive_bytes:
        raise KmlImportError("KML/KMZ archive size limit exceeded")

    documents: list[tuple[str, bytes]]
    if display_name.lower().endswith(".kmz") or data[:4] == b"PK\x03\x04":
        try:
            with ZipFile(BytesIO(data)) as archive:
                candidates = _safe_kml_members(archive, limits)
                documents = _select_kmz_document(candidates, kmz_kml_filename)
        except BadZipFile as error:
            raise KmlImportError("invalid KMZ archive") from error
    else:
        documents = [(display_name, data)]

    points: list[ImportedPoint] = []
    lines: list[ImportedLine] = []
    polygons: list[ImportedPolygon] = []
    warnings: list[str] = []
    total_coordinates = 0
    for _, document in documents:
        (
            parsed_points,
            parsed_lines,
            parsed_polygons,
            parsed_warnings,
            count,
        ) = _parse_kml(document, limits)
        total_coordinates += count
        if total_coordinates > limits.max_coordinates:
            raise KmlImportError("KML coordinate limit exceeded")
        points.extend(parsed_points)
        lines.extend(parsed_lines)
        polygons.extend(parsed_polygons)
        warnings.extend(parsed_warnings)
    return KmlImportResult(
        points=tuple(points),
        lines=tuple(lines),
        polygons=tuple(polygons),
        warnings=tuple(warnings),
        source_files=tuple(name for name, _ in documents),
    )


def import_kml_text(
    source: str,
    *,
    filename: str = "pasted.kml",
    limits: ImportLimits | None = None,
) -> KmlImportResult:
    """Import KML/XML pasted into a text area without treating it as a file path."""

    if not source.lstrip("\ufeff \t\r\n").startswith("<"):
        raise KmlImportError("pasted text is not a KML document")
    return import_kml_or_kmz(
        source.encode("utf-8"),
        filename=filename,
        limits=limits,
    )
