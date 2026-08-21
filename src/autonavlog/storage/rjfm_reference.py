from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

from geographiclib.geodesic import Geodesic
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RjfmReferenceDataError(ValueError):
    """The versioned RJFM rule pack is absent, corrupt, or internally inconsistent."""


class RjfmReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class GeoPoint(RjfmReferenceModel):
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)


class PixelPoint(RjfmReferenceModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class SourceArtifact(RjfmReferenceModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    distribution: Literal[
        "ATTACHED_DOCUMENT",
        "OFFICIAL_MLIT",
        "OFFICIAL_GSI_API",
        "PUBLIC_AIP_MIRROR",
        "USER_DECISION",
    ]
    url: str | None = None
    effective_date: str | None = None
    retrieved_date: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pages_or_sections: list[str] = Field(min_length=1)
    notes: str = Field(min_length=1)


class AirportReference(RjfmReferenceModel):
    icao: Literal["RJFM"] = "RJFM"
    arp: GeoPoint
    elevation_ft_msl: float
    traffic_pattern_altitude_ft_msl: float
    source_ids: list[str] = Field(min_length=1)


class RunwayEndReference(RjfmReferenceModel):
    id: Literal["09", "27"]
    threshold: GeoPoint
    true_bearing_deg: float = Field(ge=0.0, lt=360.0)
    threshold_elevation_ft_msl: float
    source_ids: list[str] = Field(min_length=1)


class RunwayReference(RjfmReferenceModel):
    designator: Literal["09/27"] = "09/27"
    length_m: float = Field(gt=0.0)
    width_m: float = Field(gt=0.0)
    ends: dict[str, RunwayEndReference]
    center: GeoPoint
    center_method: Literal["WGS84_GEODESIC_MIDPOINT_OF_THRESHOLDS"]

    @model_validator(mode="after")
    def validate_ends_and_center(self) -> RunwayReference:
        if set(self.ends) != {"09", "27"}:
            raise ValueError("runway ends must contain exactly 09 and 27")
        if any(key != end.id for key, end in self.ends.items()):
            raise ValueError("runway end keys must match their IDs")
        threshold_09 = self.ends["09"].threshold
        threshold_27 = self.ends["27"].threshold
        inverse = Geodesic.WGS84.Inverse(
            threshold_09.latitude_deg,
            threshold_09.longitude_deg,
            threshold_27.latitude_deg,
            threshold_27.longitude_deg,
        )
        midpoint = Geodesic.WGS84.Direct(
            threshold_09.latitude_deg,
            threshold_09.longitude_deg,
            inverse["azi1"],
            inverse["s12"] / 2.0,
        )
        center_error_m = Geodesic.WGS84.Inverse(
            self.center.latitude_deg,
            self.center.longitude_deg,
            midpoint["lat2"],
            midpoint["lon2"],
        )["s12"]
        if center_error_m > 0.01:
            raise ValueError("runway center is not the WGS84 geodesic threshold midpoint")
        if abs(inverse["s12"] - self.length_m) > 1.0:
            raise ValueError("runway threshold separation does not match declared length")
        return self


class RadioNavaidReference(RjfmReferenceModel):
    identifier: Literal["MZE"] = "MZE"
    frequency_mhz: float = Field(gt=0.0)
    position: GeoPoint
    elevation_ft_msl: float
    station_declination_deg: float = Field(ge=-180.0, le=180.0)
    station_declination_epoch: int = Field(ge=1900, le=2200)
    declination_convention: Literal["EAST_POSITIVE_WEST_NEGATIVE"]
    source_ids: list[str] = Field(min_length=1)


class AffinePixelToWgs84(RjfmReferenceModel):
    type: Literal["AFFINE_PIXEL_TO_WGS84"] = "AFFINE_PIXEL_TO_WGS84"
    latitude_deg_coefficients: list[float] = Field(min_length=3, max_length=3)
    longitude_deg_coefficients: list[float] = Field(min_length=3, max_length=3)

    def apply(self, pixel: PixelPoint) -> GeoPoint:
        lat_x, lat_y, lat_offset = self.latitude_deg_coefficients
        lon_x, lon_y, lon_offset = self.longitude_deg_coefficients
        return GeoPoint(
            latitude_deg=lat_x * pixel.x + lat_y * pixel.y + lat_offset,
            longitude_deg=lon_x * pixel.x + lon_y * pixel.y + lon_offset,
        )


class MapControlPoint(RjfmReferenceModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    pixel: PixelPoint
    position: GeoPoint
    residual_nm: float = Field(ge=0.0)
    coordinate_source_id: str = Field(min_length=1)


class MapGeoreference(RjfmReferenceModel):
    source_id: str = Field(min_length=1)
    source_page: int = Field(ge=1)
    rendered_width_px: int = Field(gt=0)
    rendered_height_px: int = Field(gt=0)
    coordinate_frame: Literal["FULL_PAGE_PIXELS_TOP_LEFT_ORIGIN"]
    target_symbol_anchor: Literal["OPEN_TRIANGLE_CENTROID"]
    transform: AffinePixelToWgs84
    control_points: list[MapControlPoint] = Field(min_length=4)
    rms_residual_nm: float = Field(ge=0.0)
    max_residual_nm: float = Field(ge=0.0)
    maximum_allowed_residual_nm: float = Field(gt=0.0, le=0.5)
    estimated_target_error_nm: float = Field(gt=0.0)
    estimated_target_error_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fit(self) -> MapGeoreference:
        ids = [control.id for control in self.control_points]
        if len(ids) != len(set(ids)):
            raise ValueError("map control point IDs must be unique")
        residuals: list[float] = []
        for control in self.control_points:
            if (
                control.pixel.x >= self.rendered_width_px
                or control.pixel.y >= self.rendered_height_px
            ):
                raise ValueError("map control pixel is outside the rendered page")
            fitted = self.transform.apply(control.pixel)
            residual_nm = (
                Geodesic.WGS84.Inverse(
                    control.position.latitude_deg,
                    control.position.longitude_deg,
                    fitted.latitude_deg,
                    fitted.longitude_deg,
                )["s12"]
                / 1852.0
            )
            if abs(residual_nm - control.residual_nm) > 1e-6:
                raise ValueError(f"stored residual does not match transform for {control.id}")
            residuals.append(residual_nm)
        calculated_rms = sqrt(sum(value * value for value in residuals) / len(residuals))
        calculated_max = max(residuals)
        if abs(calculated_rms - self.rms_residual_nm) > 1e-6:
            raise ValueError("stored map RMS residual does not match transform")
        if abs(calculated_max - self.max_residual_nm) > 1e-6:
            raise ValueError("stored map maximum residual does not match transform")
        if self.max_residual_nm > self.maximum_allowed_residual_nm:
            raise ValueError("map georeference exceeds maximum allowed residual")
        return self


class DigitizedRoutePoint(RjfmReferenceModel):
    id: Literal["UMK", "OVER_FIELD", "OMARU"]
    name: str = Field(min_length=1)
    position: GeoPoint
    map_pixel: PixelPoint
    estimated_error_nm: float = Field(gt=0.0)
    validation_status: Literal["UNVERIFIED_MAP_DIGITIZATION"]
    source_ids: list[str] = Field(min_length=1)
    notes: str = Field(min_length=1)


class PcaReference(RjfmReferenceModel):
    name: Literal["MIYAZAKI_SPECIAL_CONTROL_AREA"]
    polygon_vertices: list[GeoPoint] = Field(min_length=4, max_length=4)
    exclusion_center: GeoPoint
    exclusion_radius_km: float = Field(gt=0.0)
    source_altitude_lower_m: float
    source_altitude_upper_m: float
    operational_altitude_lower_ft_msl: float
    operational_altitude_upper_ft_msl: float
    altitude_bounds_inclusive: bool
    operational_altitude_policy_status: Literal[
        "USER_APPROVED_NOT_EXACT_METRIC_CONVERSION"
    ]
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> PcaReference:
        if self.source_altitude_lower_m >= self.source_altitude_upper_m:
            raise ValueError("PCA source altitude bounds are reversed")
        if self.operational_altitude_lower_ft_msl >= self.operational_altitude_upper_ft_msl:
            raise ValueError("PCA operational altitude bounds are reversed")
        if not self.altitude_bounds_inclusive:
            raise ValueError("implemented PCA altitude bounds are inclusive")
        return self


class GsiGeoJsonTileReference(RjfmReferenceModel):
    zoom: Literal[8]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    expected_polygon_names: list[str] = Field(min_length=1, max_length=16)


class CivilTrainingTestAirspaceReference(RjfmReferenceModel):
    name: Literal["GSI_CIVIL_TRAINING_TEST_AIRSPACE"]
    data_use: Literal["DISPLAY_ONLY_LIVE_REFERENCE"]
    content_fingerprint_scope: Literal[
        "CONFIGURATION_ONLY_LIVE_GEOJSON_EXCLUDED"
    ]
    source_page_url: str = Field(min_length=1)
    layer_metadata_url: str = Field(min_length=1)
    tile_url_template: str = Field(min_length=1)
    tiles: list[GsiGeoJsonTileReference] = Field(min_length=2, max_length=2)
    feature_name_prefix: Literal["KS4-"]
    checked_at_utc: datetime
    caution_jp: str = Field(min_length=1, max_length=300)
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_live_display_reference(self) -> CivilTrainingTestAirspaceReference:
        expected_urls = {
            "source_page_url": "https://www.mlit.go.jp/koku/koku_tk10_000004.html",
            "layer_metadata_url": (
                "https://maps.gsi.go.jp/development/ichiran.html"
                "#kokuarea_minkankunren"
            ),
            "tile_url_template": (
                "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/"
                "{z}/{x}/{y}.geojson"
            ),
        }
        mismatched_urls = [
            name
            for name, expected in expected_urls.items()
            if getattr(self, name) != expected
        ]
        if mismatched_urls:
            raise ValueError(
                "civil training airspace reference uses an unapproved URL: "
                + ", ".join(mismatched_urls)
            )
        expected_polygon_names_by_tile = {
            (8, 221, 103): ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"],
            (8, 221, 104): [
                "KS4-2",
                "KS4-7",
                "KS4-6",
                "KS4-1/4",
                "KS4-1",
                "KS4-3",
                "KS4-5",
                "KS4-8",
            ],
        }
        tiles_by_coordinate: dict[
            tuple[int, int, int], GsiGeoJsonTileReference
        ] = {
            (tile.zoom, tile.x, tile.y): tile for tile in self.tiles
        }
        if set(tiles_by_coordinate) != set(expected_polygon_names_by_tile):
            raise ValueError("civil training airspace tiles must cover RJFM at GSI z8")
        for coordinate, expected_names in expected_polygon_names_by_tile.items():
            if tiles_by_coordinate[coordinate].expected_polygon_names != expected_names:
                raise ValueError(
                    "civil training airspace expected Polygon names do not match "
                    f"the checked GSI tile: {coordinate}"
                )
        required_source_ids = {
            "mlit-civil-training-test-airspace-map-2026-08-17",
            "mlit-gsi-boundary-caution-2026-08-17",
            "gsi-civil-training-test-airspace-geojson-2026-08-17",
        }
        if len(self.source_ids) != len(required_source_ids) or set(
            self.source_ids
        ) != required_source_ids:
            raise ValueError(
                "civil training airspace reference must cite the approved MLIT/GSI sources"
            )
        required_caution_phrases = (
            "参照専用",
            "NAV LOG計算",
            "PCA判定",
        )
        if not all(phrase in self.caution_jp for phrase in required_caution_phrases):
            raise ValueError(
                "civil training airspace caution must preserve display-only limitations"
            )
        if (
            self.checked_at_utc.tzinfo is None
            or self.checked_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("civil training airspace checked_at_utc must be UTC")
        return self


class MagneticCourseInterval(RjfmReferenceModel):
    lower_deg: float = Field(ge=0.0, le=360.0)
    upper_deg: float = Field(ge=0.0, le=360.0)
    lower_inclusive: bool
    upper_inclusive: bool

    @model_validator(mode="after")
    def validate_interval(self) -> MagneticCourseInterval:
        if self.lower_deg >= self.upper_deg:
            raise ValueError("magnetic-course interval must not wrap")
        return self


class RunwayDeparturePolicy(RjfmReferenceModel):
    runway_id: Literal["09", "27"]
    initial_magnetic_course_deg: float = Field(ge=0.0, lt=360.0)
    initial_straight_distance_nm: float | None = Field(default=None, gt=0.0)
    initial_straight_until_altitude_ft_msl: float | None = Field(default=None, gt=0.0)
    initial_turn_direction: Literal["LEFT", "RIGHT"]
    initial_turn_angle_deg: float = Field(gt=0.0, lt=360.0)
    post_cut_magnetic_course_deg: float = Field(ge=0.0, lt=360.0)
    extension_turn_direction: Literal["LEFT", "RIGHT"]

    @model_validator(mode="after")
    def validate_initial_straight_end(self) -> RunwayDeparturePolicy:
        if (self.initial_straight_distance_nm is None) == (
            self.initial_straight_until_altitude_ft_msl is None
        ):
            raise ValueError("runway policy must define exactly one initial-straight end condition")
        if self.initial_turn_angle_deg != 45.0:
            raise ValueError("implemented initial runway turn angle is 45 degrees")
        signed_angle = (
            -self.initial_turn_angle_deg
            if self.initial_turn_direction == "LEFT"
            else self.initial_turn_angle_deg
        )
        expected_post_cut = (self.initial_magnetic_course_deg + signed_angle) % 360.0
        if expected_post_cut != self.post_cut_magnetic_course_deg:
            raise ValueError(
                "post-cut magnetic course does not match the configured runway turn"
            )
        return self


class RjfmDeparturePolicy(RjfmReferenceModel):
    scope: Literal["RJFM_NORTHBOUND_OITA_ONLY"]
    target_altitude_ft_msl: float
    southbound_reference_altitude_ft_msl: float
    center_route_sequence: list[str] = Field(min_length=3, max_length=3)
    trigger_point_ids: list[str] = Field(min_length=2, max_length=2)
    trigger_radius_nm: float = Field(gt=0.0)
    runways: dict[str, RunwayDeparturePolicy]
    turn_bank_angle_deg: float = Field(gt=0.0, lt=90.0)
    post_cut_straight_allowed_magnetic_courses: list[MagneticCourseInterval] = Field(
        min_length=2,
        max_length=2,
    )
    target_position_tolerance_nm: float = Field(gt=0.0)
    target_altitude_tolerance_ft: float = Field(gt=0.0)
    tangent_course_tolerance_deg: float = Field(gt=0.0)
    pca_violation_severity: Literal["HARD_INVALID"]
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy(self) -> RjfmDeparturePolicy:
        if self.center_route_sequence != ["UMK", "OVER_FIELD", "OMARU"]:
            raise ValueError("CENTER route sequence must be UMK, OVER_FIELD, OMARU")
        if self.trigger_point_ids != ["UMK", "OMARU"]:
            raise ValueError("trigger point IDs must be UMK and OMARU")
        if set(self.runways) != {"09", "27"}:
            raise ValueError("departure policies must contain exactly runway 09 and 27")
        if any(key != policy.runway_id for key, policy in self.runways.items()):
            raise ValueError("departure policy keys must match runway IDs")
        extension_directions = {
            runway: policy.extension_turn_direction
            for runway, policy in self.runways.items()
        }
        if extension_directions != {"09": "LEFT", "27": "RIGHT"}:
            raise ValueError(
                "extension-turn directions must be LEFT for RWY09 and RIGHT for RWY27"
            )
        fixed_values = {
            "target_altitude_ft_msl": (self.target_altitude_ft_msl, 5500.0),
            "trigger_radius_nm": (self.trigger_radius_nm, 1.0),
            "turn_bank_angle_deg": (self.turn_bank_angle_deg, 20.0),
            "target_position_tolerance_nm": (
                self.target_position_tolerance_nm,
                0.01,
            ),
            "target_altitude_tolerance_ft": (
                self.target_altitude_tolerance_ft,
                10.0,
            ),
            "tangent_course_tolerance_deg": (
                self.tangent_course_tolerance_deg,
                0.1,
            ),
        }
        mismatched = [
            name for name, (actual, expected) in fixed_values.items() if actual != expected
        ]
        if mismatched:
            raise ValueError(
                "RJFM policy does not match implemented fixed values: "
                + ", ".join(mismatched)
            )
        course_intervals = [
            (
                interval.lower_deg,
                interval.upper_deg,
                interval.lower_inclusive,
                interval.upper_inclusive,
            )
            for interval in self.post_cut_straight_allowed_magnetic_courses
        ]
        if course_intervals != [
            (0.0, 92.0, True, False),
            (272.0, 360.0, False, False),
        ]:
            raise ValueError(
                "RJFM magnetic-course intervals do not match implemented boundaries"
            )
        return self


class RjfmReferenceData(RjfmReferenceModel):
    schema_version: Literal[2] = 2
    validation_status: Literal["SOURCE_BACKED_WITH_UNVERIFIED_MAP_POINTS"]
    sources: list[SourceArtifact] = Field(min_length=1)
    airport: AirportReference
    runway: RunwayReference
    mze: RadioNavaidReference
    georeferencing: MapGeoreference
    points: dict[str, DigitizedRoutePoint]
    pca: PcaReference
    civil_training_test_airspace: CivilTrainingTestAirspaceReference
    policy: RjfmDeparturePolicy

    @model_validator(mode="after")
    def validate_cross_references(self) -> RjfmReferenceData:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source artifact IDs must be unique")
        known_sources = set(source_ids)
        referenced_sources = {
            *self.airport.source_ids,
            *(source for end in self.runway.ends.values() for source in end.source_ids),
            *self.mze.source_ids,
            self.georeferencing.source_id,
            *(control.coordinate_source_id for control in self.georeferencing.control_points),
            *(source for point in self.points.values() for source in point.source_ids),
            *self.pca.source_ids,
            *self.civil_training_test_airspace.source_ids,
            *self.policy.source_ids,
        }
        unknown_sources = referenced_sources - known_sources
        if unknown_sources:
            raise ValueError(f"unknown source artifact IDs: {sorted(unknown_sources)}")
        expected_points = {"UMK", "OVER_FIELD", "OMARU"}
        if set(self.points) != expected_points:
            raise ValueError("route points must contain exactly UMK, OVER_FIELD, and OMARU")
        if any(key != point.id for key, point in self.points.items()):
            raise ValueError("route point keys must match their IDs")
        for point in self.points.values():
            if (
                point.map_pixel.x >= self.georeferencing.rendered_width_px
                or point.map_pixel.y >= self.georeferencing.rendered_height_px
            ):
                raise ValueError(f"map pixel is outside rendered page for {point.id}")
            fitted = self.georeferencing.transform.apply(point.map_pixel)
            error_m = Geodesic.WGS84.Inverse(
                point.position.latitude_deg,
                point.position.longitude_deg,
                fitted.latitude_deg,
                fitted.longitude_deg,
            )["s12"]
            if error_m > 0.01:
                raise ValueError(f"digitized coordinate does not match transform for {point.id}")
            minimum_estimated_error_nm = max(
                self.georeferencing.max_residual_nm,
                self.georeferencing.estimated_target_error_nm,
            )
            if point.estimated_error_nm < minimum_estimated_error_nm:
                raise ValueError(
                    "estimated error understates the georeference target uncertainty "
                    f"for {point.id}"
                )
        return self


class RjfmPayloadManifest(RjfmReferenceModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RjfmPackManifest(RjfmReferenceModel):
    schema_version: Literal[1] = 1
    dataset_id: Literal["autonavlog-rjfm-departure-guidance"]
    revision: str = Field(min_length=1)
    created_at_utc: datetime
    payload: RjfmPayloadManifest

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at_utc must be timezone-aware UTC")
        return value


RjfmModelT = TypeVar("RjfmModelT", bound=BaseModel)


def _reject_constant(value: str) -> Any:
    raise RjfmReferenceDataError(f"non-finite JSON value is not allowed: {value}")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise RjfmReferenceDataError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _load_json_model(path: Path, model: type[RjfmModelT]) -> RjfmModelT:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return model.model_validate_json(serialized)
    except RjfmReferenceDataError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        raise RjfmReferenceDataError(f"invalid RJFM JSON model: {path}") from error


def _safe_payload_path(root: Path, relative: str) -> Path:
    if "\\" in relative or any(ord(character) < 32 for character in relative):
        raise RjfmReferenceDataError("unsafe RJFM payload path")
    normalized = PurePosixPath(relative)
    if (
        normalized.is_absolute()
        or len(normalized.parts) != 1
        or normalized.name in {"", ".", ".."}
        or ":" in normalized.name
    ):
        raise RjfmReferenceDataError("unsafe RJFM payload path")
    path = root / normalized.name
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RjfmReferenceDataError("RJFM payload path escapes pack directory") from error
    if path.is_symlink():
        raise RjfmReferenceDataError("RJFM payload must not be a symbolic link")
    if not path.is_file():
        raise RjfmReferenceDataError("RJFM payload is absent")
    return path


@dataclass(frozen=True)
class RjfmReferencePack:
    manifest: RjfmPackManifest
    data: RjfmReferenceData
    content_fingerprint: str

    @classmethod
    def from_directory(cls, directory: str | Path) -> RjfmReferencePack:
        root = Path(directory)
        manifest_path = root / "manifest.json"
        if manifest_path.is_symlink():
            raise RjfmReferenceDataError("RJFM manifest must not be a symbolic link")
        if not manifest_path.is_file():
            raise RjfmReferenceDataError("RJFM manifest is absent")
        manifest = _load_json_model(manifest_path, RjfmPackManifest)
        payload_path = _safe_payload_path(root, manifest.payload.path)
        try:
            payload_bytes = payload_path.read_bytes()
        except OSError as error:
            raise RjfmReferenceDataError("RJFM payload cannot be read") from error
        digest = hashlib.sha256(payload_bytes).hexdigest()
        if digest != manifest.payload.sha256:
            raise RjfmReferenceDataError("SHA-256 mismatch for RJFM payload")
        data = _load_json_model(payload_path, RjfmReferenceData)
        return cls(manifest=manifest, data=data, content_fingerprint=digest)

    @property
    def revision(self) -> str:
        return self.manifest.revision

    @property
    def airport(self) -> AirportReference:
        return self.data.airport

    @property
    def runway(self) -> RunwayReference:
        return self.data.runway

    @property
    def mze(self) -> RadioNavaidReference:
        return self.data.mze

    @property
    def points(self) -> dict[str, DigitizedRoutePoint]:
        return self.data.points

    @property
    def pca(self) -> PcaReference:
        return self.data.pca

    @property
    def civil_training_test_airspace(self) -> CivilTrainingTestAirspaceReference:
        return self.data.civil_training_test_airspace

    @property
    def policy(self) -> RjfmDeparturePolicy:
        return self.data.policy


__all__ = [
    "AirportReference",
    "CivilTrainingTestAirspaceReference",
    "DigitizedRoutePoint",
    "GeoPoint",
    "MapGeoreference",
    "PcaReference",
    "RadioNavaidReference",
    "RjfmDeparturePolicy",
    "RjfmReferenceData",
    "RjfmReferenceDataError",
    "RjfmReferencePack",
    "RunwayEndReference",
    "RunwayReference",
]
