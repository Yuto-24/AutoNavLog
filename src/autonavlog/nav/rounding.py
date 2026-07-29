from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: float, quantum: float) -> float:
    if quantum <= 0:
        raise ValueError("rounding quantum must be positive")
    scaled = Decimal(str(value)) / Decimal(str(quantum))
    return float(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(quantum)))


class DisplayRoundingPolicy:
    bearing_deg = 1.0
    distance_nm = 0.5
    time_minutes = 0.5
    wind_speed_kt = 1.0
    temperature_c = 1.0
    qnh_hpa = 1.0
    fuel_gal = 0.1

    def bearing(self, value: float) -> float:
        return round_half_up(value % 360, self.bearing_deg) % 360

    def distance(self, value: float) -> float:
        return round_half_up(value, self.distance_nm)

    def duration_minutes(self, seconds: float) -> float:
        return round_half_up(seconds / 60.0, self.time_minutes)

    def wind(self, value: float) -> float:
        return round_half_up(value, self.wind_speed_kt)

    def temperature(self, value: float) -> float:
        return round_half_up(value, self.temperature_c)

    def qnh(self, value: float) -> float:
        return round_half_up(value, self.qnh_hpa)

    def fuel(self, value: float) -> float:
        return round_half_up(value, self.fuel_gal)
