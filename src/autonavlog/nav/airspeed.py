from __future__ import annotations

from math import ceil, floor, isfinite, sqrt

from autonavlog.domain.enums import Pa500Policy

T0_K = 288.15
P0_PA = 101325.0
LAPSE_K_PER_M = 0.0065
G0 = 9.80665
R_AIR = 287.05287
GAMMA = 1.4
FT_TO_M = 0.3048
KT_TO_MPS = 0.5144444444444445
MPS_TO_KT = 1.0 / KT_TO_MPS


def _standard_pressure_pa(pressure_altitude_ft: float) -> float:
    altitude_m = pressure_altitude_ft * FT_TO_M
    base = 1.0 - LAPSE_K_PER_M * altitude_m / T0_K
    if base <= 0:
        raise ValueError("pressure altitude is outside the supported atmosphere")
    return float(P0_PA * base ** (G0 / (R_AIR * LAPSE_K_PER_M)))


def pressure_altitude_planning_ft(
    exact_ft: float,
    policy: Pa500Policy = Pa500Policy.CEILING,
) -> float:
    units = exact_ft / 500.0
    if policy == Pa500Policy.CEILING:
        return float(ceil(units) * 500)
    if policy == Pa500Policy.FLOOR:
        return float(floor(units) * 500)
    return float(floor(units + 0.5) * 500)


def isa_temperature_c(pressure_altitude_ft: float) -> float:
    return 15.0 - LAPSE_K_PER_M * pressure_altitude_ft * FT_TO_M


def cas_from_tas(tas_kt: float, pressure_altitude_ft: float, temperature_c: float) -> float:
    if tas_kt <= 0 or not all(isfinite(v) for v in (tas_kt, pressure_altitude_ft, temperature_c)):
        raise ValueError("airspeed inputs must be finite and TAS must be positive")
    temperature_k = temperature_c + 273.15
    if temperature_k <= 0:
        raise ValueError("temperature must be above absolute zero")
    pressure = _standard_pressure_pa(pressure_altitude_ft)
    speed_of_sound = sqrt(GAMMA * R_AIR * temperature_k)
    mach = tas_kt * KT_TO_MPS / speed_of_sound
    impact_pressure = pressure * (
        (1.0 + (GAMMA - 1.0) * mach * mach / 2.0) ** (GAMMA / (GAMMA - 1.0)) - 1.0
    )
    sea_level_mach_sq = (2.0 / (GAMMA - 1.0)) * (
        (impact_pressure / P0_PA + 1.0) ** ((GAMMA - 1.0) / GAMMA) - 1.0
    )
    return sqrt(max(0.0, sea_level_mach_sq)) * sqrt(GAMMA * R_AIR * T0_K) * MPS_TO_KT


def tas_from_cas(cas_kt: float, pressure_altitude_ft: float, temperature_c: float) -> float:
    if cas_kt <= 0 or not all(isfinite(v) for v in (cas_kt, pressure_altitude_ft, temperature_c)):
        raise ValueError("airspeed inputs must be finite and CAS must be positive")
    temperature_k = temperature_c + 273.15
    if temperature_k <= 0:
        raise ValueError("temperature must be above absolute zero")
    sea_level_mach = cas_kt * KT_TO_MPS / sqrt(GAMMA * R_AIR * T0_K)
    impact_pressure = P0_PA * (
        (1.0 + (GAMMA - 1.0) * sea_level_mach * sea_level_mach / 2.0) ** (GAMMA / (GAMMA - 1.0))
        - 1.0
    )
    pressure = _standard_pressure_pa(pressure_altitude_ft)
    local_mach_sq = (2.0 / (GAMMA - 1.0)) * (
        (impact_pressure / pressure + 1.0) ** ((GAMMA - 1.0) / GAMMA) - 1.0
    )
    return sqrt(max(0.0, local_mach_sq)) * sqrt(GAMMA * R_AIR * temperature_k) * MPS_TO_KT
