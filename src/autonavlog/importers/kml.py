from __future__ import annotations

import re
import stat
import unicodedata
from dataclasses import dataclass, replace
from io import BytesIO
from math import cos, floor, hypot, isfinite, radians, sin
from pathlib import Path, PurePosixPath
from typing import Literal
from xml.etree.ElementTree import Element, ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from autonavlog.nav.geodesy import geodesic_leg

_ROUTE_NAME_SEPARATOR = re.compile(r"\s*[～〜~→⇒]\s*")
_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024
_CONNECTED_LINE_JOIN_LIMIT_NM = 0.02
_CONNECTED_LINE_JOIN_WARNING_METERS = 10.0
# Unit-sphere cells are about 64 m wide; a 0.02 NM (37 m) match is in a neighboring cell.
_POINT_BUCKET_SIZE = 1e-5


class KmlImportError(ValueError):
    pass


class KmlRouteCoordinateLimitExceeded(KmlImportError):
    def __init__(
        self,
        coordinate_count: int,
        maximum: int,
        *,
        route_kind: str = "route",
    ) -> None:
        super().__init__(
            f"selected {route_kind} coordinate limit exceeded: {coordinate_count} > {maximum}"
        )
        self.coordinate_count = coordinate_count
        self.maximum = maximum


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
    container_path: tuple[str, ...] = ()
    document_order: int = 0


@dataclass(frozen=True)
class ImportedLine:
    name: str
    coordinates: tuple[tuple[float, float], ...]
    display_coordinates: tuple[tuple[float, float], ...] = ()
    original_coordinate_count: int = 0
    container_path: tuple[str, ...] = ()
    document_order: int = 0

    def __post_init__(self) -> None:
        if not self.display_coordinates:
            object.__setattr__(self, "display_coordinates", self.coordinates)
        if not self.original_coordinate_count:
            object.__setattr__(self, "original_coordinate_count", len(self.coordinates))


@dataclass(frozen=True)
class ImportedConnectedLine:
    name: str
    container_path: tuple[str, ...]
    segment_names: tuple[str, ...]
    segment_indices: tuple[int, ...]
    coordinates: tuple[tuple[float, float], ...]
    waypoint_names: tuple[str | None, ...]
    waypoint_sources: tuple[Literal["point", "line"] | None, ...]
    distance_nm: float
    max_join_gap_nm: float
    display_coordinates: tuple[tuple[float, float], ...] = ()
    original_coordinate_count: int = 0
    document_order: int = 0

    def __post_init__(self) -> None:
        if len(self.coordinates) != len(self.waypoint_names):
            raise ValueError("connected LineString coordinates and waypoint names must align")
        if len(self.coordinates) != len(self.waypoint_sources):
            raise ValueError("connected LineString coordinates and waypoint sources must align")
        if any(
            (name is None) != (source is None)
            for name, source in zip(self.waypoint_names, self.waypoint_sources, strict=True)
        ):
            raise ValueError("connected LineString waypoint names and sources must align")
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
    container_path: tuple[str, ...] = ()
    document_order: int = 0

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
    connected_lines: tuple[ImportedConnectedLine, ...] = ()


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


def waypoint_name_slots_from_line(line: ImportedLine) -> tuple[str | None, ...]:
    """Return an index-aligned name slot for every selected coordinate."""

    slots: list[str | None] = [None] * len(line.coordinates)
    names = tuple(part.strip() for part in _ROUTE_NAME_SEPARATOR.split(line.name) if part.strip())
    if len(names) < 2 or line.original_coordinate_count != len(line.coordinates):
        return tuple(slots)
    if len(names) == len(line.coordinates):
        offset = 0
    elif len(names) <= len(line.coordinates) - 2:
        offset = 1
    else:
        return tuple(slots)
    slots[offset : offset + len(names)] = names
    return tuple(slots)


def named_waypoints_from_line(line: ImportedLine) -> tuple[ImportedPoint, ...]:
    """Map ordered names to unambiguous LineString coordinates.

    A full list maps one-to-one. A shorter list maps from the first intermediate
    coordinate, leaving the endpoints and any trailing operational points
    unnamed. Names are not adopted after coordinate deduplication because that
    can shift a valid label onto the wrong location.
    """

    return tuple(
        ImportedPoint(
            name=name,
            latitude_deg=line.coordinates[index][0],
            longitude_deg=line.coordinates[index][1],
        )
        for index, name in enumerate(waypoint_name_slots_from_line(line))
        if name is not None
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


def _direct_element_name(element: Element) -> str:
    name_element = next(
        (child for child in element if _local_name(child.tag) == "name"),
        None,
    )
    name = "" if name_element is None else (name_element.text or "").strip()
    return name or f"Unnamed {_local_name(element.tag)}"


def _container_context(
    placemark: Element,
    parents: dict[Element, Element],
) -> tuple[Element | None, tuple[str, ...]]:
    nearest: Element | None = None
    ancestry: list[Element] = []
    current = parents.get(placemark)
    while current is not None:
        if _local_name(current.tag) in {"Document", "Folder"}:
            if nearest is None:
                nearest = current
            ancestry.append(current)
        current = parents.get(current)
    ancestry.reverse()
    return nearest, tuple(_direct_element_name(item) for item in ancestry)


def _coordinate_separation_nm(
    left: tuple[float, float],
    right: tuple[float, float],
) -> float:
    return geodesic_leg(left[0], left[1], right[0], right[1]).distance_nm


def _point_bucket_key(coordinate: tuple[float, float]) -> tuple[int, int, int]:
    latitude = radians(coordinate[0])
    longitude = radians(coordinate[1])
    cos_latitude = cos(latitude)
    return (
        floor(cos_latitude * cos(longitude) / _POINT_BUCKET_SIZE),
        floor(cos_latitude * sin(longitude) / _POINT_BUCKET_SIZE),
        floor(sin(latitude) / _POINT_BUCKET_SIZE),
    )


class _PointSpatialIndex:
    def __init__(self, points: list[ImportedPoint]) -> None:
        self._buckets: dict[tuple[int, int, int], list[ImportedPoint]] = {}
        for point in points:
            coordinate = (point.latitude_deg, point.longitude_deg)
            self._buckets.setdefault(_point_bucket_key(coordinate), []).append(point)

    def nearby(self, coordinate: tuple[float, float]) -> list[ImportedPoint]:
        center = _point_bucket_key(coordinate)
        nearby: list[ImportedPoint] = []
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for z_offset in (-1, 0, 1):
                    nearby.extend(
                        self._buckets.get(
                            (
                                center[0] + x_offset,
                                center[1] + y_offset,
                                center[2] + z_offset,
                            ),
                            (),
                        )
                    )
        return nearby


def _matching_junction_point(
    points: _PointSpatialIndex | None,
    previous_end: tuple[float, float],
    next_start: tuple[float, float],
) -> ImportedPoint | None:
    if points is None:
        return None
    matches: list[tuple[float, float, int, int, ImportedPoint]] = []
    for index, point in enumerate(points.nearby(previous_end)):
        coordinate = (point.latitude_deg, point.longitude_deg)
        previous_distance = _coordinate_separation_nm(previous_end, coordinate)
        next_distance = _coordinate_separation_nm(next_start, coordinate)
        if (
            previous_distance <= _CONNECTED_LINE_JOIN_LIMIT_NM
            and next_distance <= _CONNECTED_LINE_JOIN_LIMIT_NM
        ):
            matches.append(
                (
                    max(previous_distance, next_distance),
                    previous_distance + next_distance,
                    point.document_order,
                    index,
                    point,
                )
            )
    return None if not matches else min(matches, key=lambda item: item[:-1])[-1]


def _matching_endpoint_point(
    points: _PointSpatialIndex | None,
    endpoint: tuple[float, float],
) -> ImportedPoint | None:
    if points is None:
        return None
    matches: list[tuple[float, int, int, ImportedPoint]] = []
    for index, point in enumerate(points.nearby(endpoint)):
        coordinate = (point.latitude_deg, point.longitude_deg)
        distance = _coordinate_separation_nm(endpoint, coordinate)
        if distance <= _CONNECTED_LINE_JOIN_LIMIT_NM:
            matches.append((distance, point.document_order, index, point))
    return None if not matches else min(matches, key=lambda item: item[:-1])[-1]


def _connected_lines(
    points: list[ImportedPoint],
    point_containers: list[Element | None],
    lines: list[ImportedLine],
    line_containers: list[Element | None],
    limits: ImportLimits,
    warnings: list[str],
) -> list[ImportedConnectedLine]:
    points_by_container: dict[Element, list[ImportedPoint]] = {}
    for point, container in zip(points, point_containers, strict=True):
        if container is not None:
            points_by_container.setdefault(container, []).append(point)
    point_indexes_by_container = {
        container: _PointSpatialIndex(container_points)
        for container, container_points in points_by_container.items()
    }

    lines_by_container: dict[Element, list[tuple[int, ImportedLine]]] = {}
    for index, (line, container) in enumerate(zip(lines, line_containers, strict=True)):
        if container is not None:
            lines_by_container.setdefault(container, []).append((index, line))

    connected: list[ImportedConnectedLine] = []
    for container, indexed_lines in lines_by_container.items():
        if len(indexed_lines) < 2:
            continue
        joins = [
            _coordinate_separation_nm(previous.coordinates[-1], following.coordinates[0])
            for (_, previous), (_, following) in zip(
                indexed_lines,
                indexed_lines[1:],
                strict=False,
            )
        ]
        invalid_joins = [
            (indexed_lines[index][1], indexed_lines[index + 1][1], gap)
            for index, gap in enumerate(joins)
            if gap > _CONNECTED_LINE_JOIN_LIMIT_NM
        ]
        container_path = indexed_lines[0][1].container_path
        display_path = " / ".join(container_path) or _direct_element_name(container)
        if invalid_joins:
            for previous, following, gap in invalid_joins:
                warnings.append(
                    f"{display_path}: LineString join {previous.name} -> {following.name} "
                    f"is {gap:.5f} NM and exceeds {_CONNECTED_LINE_JOIN_LIMIT_NM:.2f} NM; "
                    "kept as individual candidates"
                )
            continue

        container_points = point_indexes_by_container.get(container)
        first_line = indexed_lines[0][1]
        coordinates = list(first_line.coordinates)
        waypoint_names = list(waypoint_name_slots_from_line(first_line))
        waypoint_sources: list[Literal["point", "line"] | None] = [
            "line" if name is not None else None for name in waypoint_names
        ]
        for join_index, ((_, previous), (_, following)) in enumerate(
            zip(indexed_lines, indexed_lines[1:], strict=False)
        ):
            junction_point = _matching_junction_point(
                container_points,
                previous.coordinates[-1],
                following.coordinates[0],
            )
            if junction_point is not None:
                coordinates[-1] = (
                    junction_point.latitude_deg,
                    junction_point.longitude_deg,
                )
                junction_name = junction_point.name.strip()
                if junction_name:
                    waypoint_names[-1] = junction_name
                    waypoint_sources[-1] = "point"
            elif joins[join_index] * 1852.0 > _CONNECTED_LINE_JOIN_WARNING_METERS:
                warnings.append(
                    f"{display_path}: merged LineString join {previous.name} -> "
                    f"{following.name} across {joins[join_index] * 1852.0:.1f} m "
                    "without a matching Point Placemark"
                )
            following_names = waypoint_name_slots_from_line(following)
            coordinates.extend(following.coordinates[1:])
            waypoint_names.extend(following_names[1:])
            waypoint_sources.extend(
                "line" if name is not None else None for name in following_names[1:]
            )

        if waypoint_names[0] is None:
            start_point = _matching_endpoint_point(container_points, coordinates[0])
            if start_point is not None:
                start_name = start_point.name.strip()
                if start_name:
                    waypoint_names[0] = start_name
                    waypoint_sources[0] = "point"
        if waypoint_names[-1] is None:
            end_point = _matching_endpoint_point(container_points, coordinates[-1])
            if end_point is not None:
                end_name = end_point.name.strip()
                if end_name:
                    waypoint_names[-1] = end_name
                    waypoint_sources[-1] = "point"

        candidate = ImportedConnectedLine(
            name=container_path[-1] if container_path else _direct_element_name(container),
            container_path=container_path,
            segment_names=tuple(line.name for _, line in indexed_lines),
            segment_indices=tuple(index for index, _ in indexed_lines),
            coordinates=tuple(coordinates),
            waypoint_names=tuple(waypoint_names),
            waypoint_sources=tuple(waypoint_sources),
            distance_nm=sum(imported_line_length_nm(line) for _, line in indexed_lines),
            max_join_gap_nm=max(joins, default=0.0),
            display_coordinates=_simplify(coordinates, limits.max_display_vertices),
            original_coordinate_count=len(coordinates),
            document_order=first_line.document_order,
        )
        try:
            _deduplicate_connected_line(candidate)
        except KmlImportError:
            warnings.append(
                f"{display_path}: connected LineStrings have fewer than two distinct points "
                "after 10 m deduplication; kept as individual candidates"
            )
            continue
        connected.append(candidate)
    return connected


def _parse_kml(
    data: bytes,
    limits: ImportLimits,
) -> tuple[
    list[ImportedPoint],
    list[ImportedLine],
    list[ImportedConnectedLine],
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
    document_order = 0
    parents = {child: parent for parent in root.iter() for child in parent}
    point_containers: list[Element | None] = []
    line_containers: list[Element | None] = []
    for placemark in (item for item in root.iter() if _local_name(item.tag) == "Placemark"):
        container, container_path = _container_context(placemark, parents)
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
            geometry_order = document_order
            document_order += 1
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
                        container_path=container_path,
                        document_order=geometry_order,
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
                points.append(
                    ImportedPoint(
                        name,
                        latitude,
                        longitude,
                        altitude,
                        container_path,
                        geometry_order,
                    )
                )
                point_containers.append(container)
            elif len(parsed) >= 2:
                coordinates = tuple((latitude, longitude) for latitude, longitude, _ in parsed)
                display = _simplify(
                    list(coordinates),
                    limits.max_display_vertices,
                )
                lines.append(
                    ImportedLine(
                        name,
                        coordinates,
                        display,
                        container_path=container_path,
                        document_order=geometry_order,
                    )
                )
                line_containers.append(container)
        if skipped_polygon_surfaces:
            warnings.append(
                f"{name}: skipped {skipped_polygon_surfaces} Polygon surface(s) "
                "without a usable horizontal boundary"
            )
    connected = _connected_lines(
        points,
        point_containers,
        lines,
        line_containers,
        limits,
        warnings,
    )
    return points, lines, connected, polygons, warnings, coordinate_count


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


def imported_line_length_nm(line: ImportedLine | ImportedConnectedLine) -> float:
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
        raise KmlRouteCoordinateLimitExceeded(
            len(line.coordinates),
            limits.max_coordinates_in_selected_line,
            route_kind="LineString",
        )
    coordinates = _deduplicate_selected_line(line.coordinates)
    return ImportedLine(
        name=line.name,
        coordinates=coordinates,
        display_coordinates=_simplify(
            list(coordinates),
            limits.max_display_vertices,
        ),
        original_coordinate_count=line.original_coordinate_count,
        container_path=line.container_path,
        document_order=line.document_order,
    )


def _deduplicate_connected_line(
    line: ImportedConnectedLine,
) -> tuple[
    tuple[tuple[float, float], ...],
    tuple[str | None, ...],
    tuple[Literal["point", "line"] | None, ...],
]:
    adopted_coordinates: list[tuple[float, float]] = []
    adopted_names: list[str | None] = []
    adopted_sources: list[Literal["point", "line"] | None] = []
    final_index = len(line.coordinates) - 1
    for index, (coordinate, name, source) in enumerate(
        zip(
            line.coordinates,
            line.waypoint_names,
            line.waypoint_sources,
            strict=True,
        )
    ):
        if not adopted_coordinates:
            adopted_coordinates.append(coordinate)
            adopted_names.append(name)
            adopted_sources.append(source)
            continue
        if (
            index == final_index
            and coordinate == line.coordinates[0]
            and len(adopted_coordinates) > 1
        ):
            adopted_coordinates.append(coordinate)
            adopted_names.append(name)
            adopted_sources.append(source)
            continue
        separation_m = _coordinate_separation_nm(adopted_coordinates[-1], coordinate) * 1852.0
        if separation_m <= _CONNECTED_LINE_JOIN_WARNING_METERS:
            if adopted_names[-1] is None and name is not None:
                adopted_names[-1] = name
                adopted_sources[-1] = source
            continue
        adopted_coordinates.append(coordinate)
        adopted_names.append(name)
        adopted_sources.append(source)
    if len(adopted_coordinates) < 2 or len(set(adopted_coordinates)) < 2:
        raise KmlImportError(
            "selected connected LineString has fewer than two points after deduplication"
        )
    return tuple(adopted_coordinates), tuple(adopted_names), tuple(adopted_sources)


def connected_line_route_shape(line: ImportedConnectedLine) -> ImportedConnectedLine:
    """Return the connected route exactly as it will be installed after 10 m deduplication."""

    coordinates, waypoint_names, waypoint_sources = _deduplicate_connected_line(line)
    return replace(
        line,
        coordinates=coordinates,
        waypoint_names=waypoint_names,
        waypoint_sources=waypoint_sources,
        display_coordinates=_simplify(list(coordinates), ImportLimits().max_display_vertices),
    )


def select_imported_connected_line(
    result: KmlImportResult,
    index: int,
    *,
    limits: ImportLimits | None = None,
) -> ImportedConnectedLine:
    limits = limits or ImportLimits()
    try:
        line = result.connected_lines[index]
    except IndexError as error:
        raise KmlImportError("selected connected LineString is unavailable") from error
    if len(line.coordinates) > limits.max_coordinates_in_selected_line:
        raise KmlRouteCoordinateLimitExceeded(
            len(line.coordinates),
            limits.max_coordinates_in_selected_line,
            route_kind="connected LineString",
        )
    selected = connected_line_route_shape(line)
    return replace(
        selected,
        display_coordinates=_simplify(list(selected.coordinates), limits.max_display_vertices),
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
    connected_lines: list[ImportedConnectedLine] = []
    polygons: list[ImportedPolygon] = []
    warnings: list[str] = []
    total_coordinates = 0
    for _, document in documents:
        (
            parsed_points,
            parsed_lines,
            parsed_connected_lines,
            parsed_polygons,
            parsed_warnings,
            count,
        ) = _parse_kml(document, limits)
        total_coordinates += count
        if total_coordinates > limits.max_coordinates:
            raise KmlImportError("KML coordinate limit exceeded")
        line_index_offset = len(lines)
        points.extend(parsed_points)
        lines.extend(parsed_lines)
        connected_lines.extend(
            replace(
                connected,
                segment_indices=tuple(
                    line_index_offset + index for index in connected.segment_indices
                ),
            )
            for connected in parsed_connected_lines
        )
        polygons.extend(parsed_polygons)
        warnings.extend(parsed_warnings)
    return KmlImportResult(
        points=tuple(points),
        lines=tuple(lines),
        connected_lines=tuple(connected_lines),
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
