"""The approved 115-row catalog is reproducible from its published inputs."""
import json
import runpy
from collections import Counter
from pathlib import Path

from geographiclib.geodesic import Geodesic

from autonavlog.domain.project import ReferencePointSnapshot

ROOT = Path(__file__).resolve().parents[2]


def test_vrep_scope_provenance_and_derived_coordinates():
    points = json.loads((ROOT / "data/reference/vrep.json").read_text())
    assert Counter(point["airport_icao"] for point in points) == {
        "RJFC": 5, "RJFE": 9, "RJFG": 5, "RJFK": 8, "RJFM": 7,
        "RJFO": 6, "RJFS": 9, "RJFT": 8, "RJFU": 10,
        "RJOA": 5, "RJOB": 5, "RJOK": 8, "RJOM": 8,
        "RJOR": 8, "RJOS": 5, "RJOT": 9,
    }
    assert len(points) == len({point["reference_id"] for point in points}) == 115
    for point in points:
        snapshot = ReferencePointSnapshot.model_validate(point)
        source = snapshot.source
        assert source.url.endswith("#issuecomment-6415872164")
        assert source.filename.startswith(snapshot.airport_icao)
        result = Geodesic.WGS84.Direct(
            source.arp_latitude_deg, source.arp_longitude_deg,
            snapshot.bearing_true_deg, snapshot.distance_nm * 1852,
        )
        assert round(result["lat2"], 6) == snapshot.latitude_deg
        assert round(result["lon2"], 6) == snapshot.longitude_deg


def test_committed_catalog_matches_confirmed_source():
    """Catch source transcription changes that have not regenerated the catalog."""
    generator = runpy.run_path(str(ROOT / "scripts/reference/build_vrep_catalog.py"))
    committed = json.loads((ROOT / "data/reference/vrep.json").read_text())
    assert committed == generator["catalog"]()
