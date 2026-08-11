from __future__ import annotations

from math import isclose

LEGAL_THRESHOLD_NOTE_JA = (
    "航空法第82条・施行規則第177条のVFR巡航高度は、地表又は水面から900 m以上で適用されます。"
)
TERRAIN_LIMITATION_NOTE_JA = (
    "本アプリは地表高を判定しないため、候補外の値は違法判定ではなく要確認表示です。"
)


def magnetic_course_deg(true_course_deg: float, variation_deg_east: float) -> float:
    return (true_course_deg + variation_deg_east) % 360.0


def vfr_cruising_altitude_candidates(
    magnetic_course: float,
    *,
    maximum_ft_msl: int = 25_000,
) -> tuple[int, ...]:
    """Return Japanese VFR cruising levels up to the supported MSL ceiling."""

    normalized_course = magnetic_course % 360.0
    start = 3_500 if normalized_course < 180.0 else 4_500
    return tuple(range(start, maximum_ft_msl + 1, 2_000))


def matches_vfr_cruising_altitude(
    altitude_ft_msl: float,
    magnetic_course: float,
) -> bool:
    candidates = vfr_cruising_altitude_candidates(magnetic_course)
    return any(isclose(altitude_ft_msl, candidate, abs_tol=0.01) for candidate in candidates)
