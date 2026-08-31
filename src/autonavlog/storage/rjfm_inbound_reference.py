"""Fail-safe versioned inputs for RJFM inbound west-extension guidance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast


class RjfmInboundReferenceError(ValueError):
    """The inbound guidance reference pack is unsafe, corrupt, or incomplete."""


@dataclass(frozen=True)
class GeoPoint:
    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True)
class InboundPolicy:
    magnetic_bearing_min_deg: float
    magnetic_bearing_max_deg: float
    coarse_bearing_step_deg: float
    dme_rounding_increment_nm: float


@dataclass(frozen=True)
class AirspaceBoundary:
    source_id: str
    revision: str
    checksum_sha256: str
    polygon_vertices: tuple[GeoPoint, ...]


@dataclass(frozen=True)
class RjfmInboundGuidanceReference:
    revision: str
    content_fingerprint: str
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    policy: InboundPolicy
    mze_position: GeoPoint
    mze_elevation_ft_msl: float
    boundary: AirspaceBoundary | None
    reason_code: str | None = None
    message: str | None = None

    @property
    def available_for_solver(self) -> bool:
        return self.status == "AVAILABLE" and self.boundary is not None

    @classmethod
    def from_directory(cls, directory: Path) -> RjfmInboundGuidanceReference:
        root = _resolved_directory(directory)
        try:
            manifest_path = _safe_file(root, PurePosixPath("manifest.json"), "reference manifest")
            manifest_raw = manifest_path.read_bytes()
        except OSError as error:
            raise RjfmInboundReferenceError("inbound reference manifest is missing") from error
        manifest = _object(manifest_raw, "manifest")
        payload = _validate_manifest(manifest)
        path = _relative_path(payload["path"], "manifest payload path")
        payload_path = _safe_file(root, path, "inbound payload")
        try:
            raw = payload_path.read_bytes()
        except OSError as error:
            raise RjfmInboundReferenceError("inbound payload is missing") from error
        expected = _sha256(payload["sha256"], "manifest payload checksum")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise RjfmInboundReferenceError("inbound payload SHA-256 mismatch")
        return _parse(_object(raw, "inbound payload"), actual, root)


def _parse(
    data: dict[str, Any],
    fingerprint: str,
    directory: Path | None = None,
) -> RjfmInboundGuidanceReference:
    if data.get("schema_version") != 1:
        raise RjfmInboundReferenceError("unsupported inbound reference schema")
    status = cast(Literal["AVAILABLE", "UNAVAILABLE"], _string(data.get("status"), "status"))
    if status not in {"AVAILABLE", "UNAVAILABLE"}:
        raise RjfmInboundReferenceError("invalid inbound reference status")
    sources = _sources(data.get("sources"))
    policy_data = _mapping(data.get("policy"), "policy")
    policy_source = _string(policy_data.get("source_id"), "policy source")
    if sources.get(policy_source, {}).get("distribution") != "USER_DECISION":
        raise RjfmInboundReferenceError("policy must cite USER_DECISION")
    policy = InboundPolicy(
        *(
            _number(policy_data.get(key), key)
            for key in (
                "magnetic_bearing_min_deg",
                "magnetic_bearing_max_deg",
                "coarse_bearing_step_deg",
                "dme_rounding_increment_nm",
            )
        )
    )
    if not (
        0 <= policy.magnetic_bearing_min_deg < policy.magnetic_bearing_max_deg <= 360
        and policy.coarse_bearing_step_deg > 0
        and policy.dme_rounding_increment_nm > 0
    ):
        raise RjfmInboundReferenceError("invalid inbound guidance policy")
    mze = _mapping(data.get("mze"), "mze")
    mze_source_id = _string(mze.get("source_id"), "mze source")
    mze_source = sources.get(mze_source_id)
    if mze_source is None:
        raise RjfmInboundReferenceError("MZE source is missing")
    mze_position = _point(mze, "mze")
    elevation = _number(mze.get("elevation_ft_msl"), "MZE elevation")
    if elevation < -1000:
        raise RjfmInboundReferenceError("MZE elevation is invalid")
    _validate_mze_source(mze_source, mze_position, elevation)
    revision = _string(data.get("revision"), "revision")

    if status == "UNAVAILABLE":
        if data.get("boundary") is not None:
            raise RjfmInboundReferenceError(
                "unavailable reference must not contain a solver boundary"
            )
        unavailable = _mapping(data.get("unavailable"), "unavailable")
        evidence = unavailable.get("evidence_source_ids")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) and item in sources for item in evidence)
        ):
            raise RjfmInboundReferenceError("unavailable evidence sources are invalid")
        return RjfmInboundGuidanceReference(
            revision,
            fingerprint,
            status,
            policy,
            mze_position,
            elevation,
            None,
            _string(unavailable.get("reason_code"), "unavailable reason"),
            _string(unavailable.get("message"), "unavailable message"),
        )

    if data.get("unavailable") is not None:
        raise RjfmInboundReferenceError("available reference must not contain unavailable evidence")
    boundary_data = _mapping(data.get("boundary"), "solver boundary")
    source_id = _string(boundary_data.get("source_id"), "boundary source")
    source = sources.get(source_id)
    if source is None:
        raise RjfmInboundReferenceError("solver boundary source identity is missing")
    checksum = _sha256(boundary_data.get("checksum_sha256"), "boundary checksum")
    revision_value = _string(boundary_data.get("revision"), "boundary revision")
    vertices = boundary_data.get("polygon_vertices")
    if not isinstance(vertices, list):
        raise RjfmInboundReferenceError("solver boundary polygon is invalid")
    points = _validate_polygon(vertices)
    _validate_primary_boundary_source(
        source,
        source_id,
        revision_value,
        checksum,
        points,
        data,
        directory,
    )
    return RjfmInboundGuidanceReference(
        revision,
        fingerprint,
        status,
        policy,
        mze_position,
        elevation,
        AirspaceBoundary(source_id, revision_value, checksum, points),
    )


def _sources(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RjfmInboundReferenceError("inbound sources are invalid")
    result: dict[str, dict[str, Any]] = {}
    for source in value:
        data = _mapping(source, "source")
        source_id = _string(data.get("id"), "source ID")
        distribution = _string(data.get("distribution"), "source distribution")
        if source_id in result or distribution not in {
            "USER_DECISION",
            "OFFICIAL_MLIT",
            "OFFICIAL_PRIMARY",
            "PUBLIC_AIP_MIRROR",
        }:
            raise RjfmInboundReferenceError("inbound source is invalid")
        checksum = data.get("sha256")
        if checksum is not None:
            _sha256(checksum, "source checksum")
        result[source_id] = data
    return result


def _validate_manifest(manifest: dict[str, Any]) -> dict[str, str]:
    if manifest.get("format_version") != 1 or isinstance(manifest.get("format_version"), bool):
        raise RjfmInboundReferenceError("unsupported inbound reference manifest schema")
    payload = _mapping(manifest.get("payload"), "manifest payload")
    if set(payload) != {"path", "sha256"}:
        raise RjfmInboundReferenceError("inbound reference manifest schema is invalid")
    return {
        "path": _string(payload.get("path"), "manifest payload path"),
        "sha256": _sha256(payload.get("sha256"), "manifest payload checksum"),
    }


def _resolved_directory(directory: Path) -> Path:
    try:
        root = directory.resolve(strict=True)
    except OSError as error:
        raise RjfmInboundReferenceError("inbound reference directory is missing") from error
    if not root.is_dir():
        raise RjfmInboundReferenceError("inbound reference directory is invalid")
    return root


def _relative_path(value: object, context: str) -> PurePosixPath:
    relative = _string(value, context)
    if "\\" in relative:
        raise RjfmInboundReferenceError(f"{context} is unsafe")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise RjfmInboundReferenceError(f"{context} is unsafe")
    return path


def _safe_file(root: Path, relative: PurePosixPath, context: str) -> Path:
    try:
        target = (root.joinpath(*relative.parts)).resolve(strict=False)
        target.relative_to(root)
    except (OSError, ValueError) as error:
        raise RjfmInboundReferenceError(f"{context} path is unsafe") from error
    return target


def _validate_mze_source(
    source: dict[str, Any], position: GeoPoint, elevation_ft_msl: float
) -> None:
    if source.get("distribution") not in {
        "PUBLIC_AIP_MIRROR",
        "OFFICIAL_PRIMARY",
        "OFFICIAL_MLIT",
    }:
        raise RjfmInboundReferenceError("MZE source is not a document source")
    _string(source.get("title"), "MZE source title")
    _string(source.get("publisher"), "MZE source publisher")
    _string(source.get("document_id"), "MZE source document ID")
    _string(source.get("revision"), "MZE source revision")
    url = _string(source.get("url"), "MZE source URL")
    if not url.startswith(("https://", "http://")):
        raise RjfmInboundReferenceError("MZE source URL is invalid")
    _validate_source_dates(source, "MZE source", require_effective=True)
    _sha256(source.get("sha256"), "MZE source checksum")
    declared_position = source.get("position")
    if declared_position is None:
        raise RjfmInboundReferenceError("MZE source position is missing")
    position_data = _mapping(declared_position, "MZE source position")
    source_position = _point(position_data, "MZE source position")
    if (
        abs(source_position.latitude_deg - position.latitude_deg) > 1e-9
        or abs(source_position.longitude_deg - position.longitude_deg) > 1e-9
    ):
        raise RjfmInboundReferenceError("MZE coordinates do not match source")
    declared_elevation = source.get("elevation_ft_msl")
    if declared_elevation is None:
        raise RjfmInboundReferenceError("MZE source elevation is missing")
    if abs(
        _number(declared_elevation, "MZE source elevation") - elevation_ft_msl
    ) > 0.1:
        raise RjfmInboundReferenceError("MZE elevation does not match source")


def _validate_source_dates(
    source: dict[str, Any], context: str, *, require_effective: bool = False
) -> None:
    effective = source.get("effective_date")
    retrieved = source.get("retrieved_date")
    if require_effective and effective is None:
        raise RjfmInboundReferenceError(f"{context} effective date is invalid")
    effective_text = (
        _date(effective, f"{context} effective date")
        if effective is not None
        else None
    )
    retrieved_text = _date(retrieved, f"{context} retrieved date")
    if effective_text is not None and date.fromisoformat(
        effective_text
    ) > date.fromisoformat(retrieved_text):
        raise RjfmInboundReferenceError(f"{context} dates are invalid")


def _validate_primary_boundary_source(
    source: dict[str, Any],
    source_id: str,
    boundary_revision: str,
    boundary_checksum: str,
    points: tuple[GeoPoint, ...],
    payload: dict[str, Any],
    directory: Path | None,
) -> None:
    if source.get("id") != source_id:
        raise RjfmInboundReferenceError("solver boundary source identity mismatch")
    category = source.get("source_category", source.get("category"))
    if category != "PRIMARY_HORIZONTAL_BOUNDARY":
        raise RjfmInboundReferenceError("solver boundary requires a primary horizontal source")
    if source.get("distribution") not in {"OFFICIAL_PRIMARY", "OFFICIAL_MLIT"}:
        raise RjfmInboundReferenceError("solver boundary source is not primary")
    if (
        source.get("display_only") is True
        or source.get("data_use") == "DISPLAY_ONLY_LIVE_REFERENCE"
    ):
        raise RjfmInboundReferenceError("display-only source cannot define solver boundary")
    if source.get("availability") not in {None, "AVAILABLE"}:
        raise RjfmInboundReferenceError("solver boundary source is unavailable")
    if source.get("status") not in {None, "AVAILABLE", "VERIFIED"}:
        raise RjfmInboundReferenceError("solver boundary source is unavailable")
    for key in ("publisher", "document_id", "url", "revision"):
        _string(source.get(key), f"boundary source {key}")
    if not str(source["url"]).startswith(("https://", "http://")):
        raise RjfmInboundReferenceError("boundary source URL is invalid")
    _validate_source_dates(source, "boundary source", require_effective=True)
    if boundary_revision != source["revision"]:
        raise RjfmInboundReferenceError("solver boundary revision does not match source")
    source_checksum = _sha256(source.get("sha256"), "boundary source checksum")
    artifact_checksum = _sha256(source.get("artifact_sha256"), "boundary source artifact checksum")
    if source_checksum != artifact_checksum or boundary_checksum != source_checksum:
        raise RjfmInboundReferenceError("solver boundary source checksum mismatch")
    if directory is None:
        raise RjfmInboundReferenceError("verified boundary source artifact is missing")
    artifact_path = _relative_path(source.get("artifact_path"), "boundary source artifact path")
    artifact = _safe_file(directory, artifact_path, "boundary source artifact")
    try:
        artifact_raw = artifact.read_bytes()
    except OSError as error:
        raise RjfmInboundReferenceError("boundary source artifact is missing") from error
    if hashlib.sha256(artifact_raw).hexdigest() != source_checksum:
        raise RjfmInboundReferenceError("boundary source artifact SHA-256 mismatch")
    if source.get("synthetic") is True or payload.get("synthetic_fixture") is True:
        raise RjfmInboundReferenceError("synthetic boundary sources are not solver references")
    _validate_coordinate_metadata(source.get("coordinate_validation"), points)


def _validate_coordinate_metadata(value: object, points: tuple[GeoPoint, ...]) -> None:
    metadata = _mapping(value, "coordinate validation")
    required = {
        "finite": True,
        "in_bounds": True,
        "non_collinear": True,
        "no_duplicate_vertices": True,
        "no_self_intersections": True,
        "nonzero_area": True,
        "ring_closed": True,
        "vertex_count": len(points),
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise RjfmInboundReferenceError("boundary coordinate validation is invalid")


def _validate_polygon(value: list[object]) -> tuple[GeoPoint, ...]:
    if len(value) < 3:
        raise RjfmInboundReferenceError("solver boundary polygon is invalid")
    points = tuple(_point(_mapping(item, "boundary vertex"), "boundary vertex") for item in value)
    ring_closed = points[0] == points[-1]
    if ring_closed:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) != len(points):
        raise RjfmInboundReferenceError("solver boundary coordinates are invalid")
    area = _polygon_area(points)
    if area <= 1e-12:
        raise RjfmInboundReferenceError("solver boundary polygon is degenerate")
    edges = tuple(zip(points, points[1:] + points[:1], strict=True))
    for index, first in enumerate(edges):
        for other_index in range(index + 1, len(edges)):
            second = edges[other_index]
            adjacent = other_index in {index + 1, index - 1} or {
                index,
                other_index,
            } == {0, len(edges) - 1}
            if _segments_intersect(first[0], first[1], second[0], second[1]):
                if adjacent and _adjacent_edges_only_meet_at_shared_endpoint(
                    first, second, points, index, other_index
                ):
                    continue
                raise RjfmInboundReferenceError("solver boundary polygon self-intersects")
    return points


def _polygon_area(points: tuple[GeoPoint, ...]) -> float:
    return (
        abs(
            sum(
                point.longitude_deg * next_point.latitude_deg
                - next_point.longitude_deg * point.latitude_deg
                for point, next_point in zip(points, points[1:] + points[:1], strict=True)
            )
        )
        / 2
    )


def _adjacent_edges_only_meet_at_shared_endpoint(
    first: tuple[GeoPoint, GeoPoint],
    second: tuple[GeoPoint, GeoPoint],
    points: tuple[GeoPoint, ...],
    first_index: int,
    second_index: int,
) -> bool:
    shared = points[second_index] if second_index == first_index + 1 else points[first_index]
    return all(
        endpoint == shared or not _on_segment(shared, endpoint, other)
        for endpoint in first + second
        for other in (first[0], first[1], second[0], second[1])
        if endpoint != other and endpoint != shared
    )


def _segments_intersect(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> bool:
    def orientation(first: GeoPoint, second: GeoPoint, third: GeoPoint) -> float:
        return (second.longitude_deg - first.longitude_deg) * (
            third.latitude_deg - first.latitude_deg
        ) - (second.latitude_deg - first.latitude_deg) * (third.longitude_deg - first.longitude_deg)

    values = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    epsilon = 1e-12
    if all(abs(value) <= epsilon for value in values):
        return not (
            max(a.longitude_deg, b.longitude_deg) < min(c.longitude_deg, d.longitude_deg)
            or max(c.longitude_deg, d.longitude_deg) < min(a.longitude_deg, b.longitude_deg)
            or max(a.latitude_deg, b.latitude_deg) < min(c.latitude_deg, d.latitude_deg)
            or max(c.latitude_deg, d.latitude_deg) < min(a.latitude_deg, b.latitude_deg)
        )

    def same_strict_sign(first: float, second: float) -> bool:
        return (first > epsilon and second > epsilon) or (first < -epsilon and second < -epsilon)

    if same_strict_sign(values[0], values[1]) or same_strict_sign(values[2], values[3]):
        return False
    return (
        max(a.longitude_deg, b.longitude_deg) + epsilon >= min(c.longitude_deg, d.longitude_deg)
        and max(c.longitude_deg, d.longitude_deg) + epsilon >= min(a.longitude_deg, b.longitude_deg)
        and max(a.latitude_deg, b.latitude_deg) + epsilon >= min(c.latitude_deg, d.latitude_deg)
        and max(c.latitude_deg, d.latitude_deg) + epsilon >= min(a.latitude_deg, b.latitude_deg)
    )


def _on_segment(start: GeoPoint, point: GeoPoint, end: GeoPoint) -> bool:
    cross = (end.longitude_deg - start.longitude_deg) * (
        point.latitude_deg - start.latitude_deg
    ) - (end.latitude_deg - start.latitude_deg) * (
        point.longitude_deg - start.longitude_deg
    )
    if abs(cross) > 1e-12:
        return False
    return min(start.latitude_deg, end.latitude_deg) <= point.latitude_deg <= max(
        start.latitude_deg, end.latitude_deg
    ) and min(start.longitude_deg, end.longitude_deg) <= point.longitude_deg <= max(
        start.longitude_deg, end.longitude_deg
    )


def _date(value: object, context: str) -> str:
    text = _string(value, context)
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise RjfmInboundReferenceError(f"{context} is invalid") from error
    return text


def _object(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RjfmInboundReferenceError(f"{context} is not valid JSON") from error
    return _mapping(value, context)


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RjfmInboundReferenceError(f"{context} is invalid")
    return cast(dict[str, Any], value)


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RjfmInboundReferenceError(f"{context} is invalid")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise RjfmInboundReferenceError(f"{context} is invalid")
    return float(value)


def _sha256(value: object, context: str) -> str:
    checksum = _string(value, context)
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise RjfmInboundReferenceError(f"{context} is invalid")
    return checksum


def _point(data: dict[str, Any], context: str) -> GeoPoint:
    latitude, longitude = (
        _number(data.get("latitude_deg"), context),
        _number(data.get("longitude_deg"), context),
    )
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise RjfmInboundReferenceError(f"{context} coordinates are invalid")
    return GeoPoint(latitude, longitude)
