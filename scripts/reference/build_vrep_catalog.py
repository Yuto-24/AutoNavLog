"""Transcribe only the approved #169 comment; never read or infer PDF coordinates."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/reference/vrep-169-confirmed-source.md"
TARGET = ROOT / "data/reference/vrep.json"
URL = "https://github.com/Yuto-24/AutoNavLog/issues/169#issuecomment-6415872164"


def catalog():
    points = []
    for block in re.split(r"^## ", SOURCE.read_text(), flags=re.M)[1:]:
        airport = block[:4]
        if not re.match(r"RJ[A-Z]{2}", airport) or airport == "RJFY":
            continue
        filename = re.search(r"資料 `([^`]+)`", block)[1]
        sha = re.search(r"SHA-256 `([^`]+)`", block)[1]
        table = re.search(r"表: PDF p(\d+), (.*?), EFF ([\d-]+)", block)
        arp = re.search(r"ARP: PDF p(\d+), EFF ([\d-]+), (\d+N) (\d+E)", block)
        lat, lon = map(float, re.search(r"ARP十進度: ([\d.]+), ([\d.]+)", block).groups())
        for line in block.splitlines():
            if not line.startswith(f"|{airport}-VREP-"):
                continue
            pid, name, japanese, bearing, distance, latitude, longitude, note = line[1:-1].split(
                "|"
            )
            points.append(
                dict(
                    reference_id=pid,
                    name=name,
                    name_ja=japanese,
                    kind="VISUAL_REPORTING_POINT",
                    airport_icao=airport,
                    latitude_deg=float(latitude),
                    longitude_deg=float(longitude),
                    bearing_true_deg=float(bearing),
                    distance_nm=float(distance),
                    note=note,
                    derivation=(
                        "Geodesic.WGS84.Direct(arp_latitude_deg, arp_longitude_deg, "
                        "bearing_true_deg, distance_nm * 1852); lat2/lon2 rounded to 6 decimals; "
                        "no magnetic variation"
                    ),
                    source=dict(
                        url=URL,
                        filename=filename,
                        sha256=sha,
                        table_pdf_page=int(table[1]),
                        table_page=table[2],
                        table_effective_date=table[3],
                        arp_pdf_page=int(arp[1]),
                        arp_effective_date=arp[2],
                        arp_latitude_dms=arp[3],
                        arp_longitude_dms=arp[4],
                        arp_latitude_deg=lat,
                        arp_longitude_deg=lon,
                    ),
                )
            )
    assert len(points) == len({p["reference_id"] for p in points}) == 115
    assert len({p["airport_icao"] for p in points}) == 16
    return points


if __name__ == "__main__":
    TARGET.write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n")
