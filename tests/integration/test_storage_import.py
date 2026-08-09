from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from autonavlog.importers.kml import (
    ImportLimits,
    KmlImportError,
    import_kml_or_kmz,
    import_kml_text,
)
from autonavlog.storage.local import LocalProjectRepository, RevisionConflictError

KML = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>TP1</name><Point><coordinates>131.5,32.0,100</coordinates></Point></Placemark>
<Placemark><name>Reference</name><LineString><coordinates>
131.5,32.0 131.6,32.1 131.7,32.2
</coordinates></LineString></Placemark>
</Document></kml>"""

GOOGLE_EARTH_3D_POLYGON = """\ufeff<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark>
  <name>KS4-6(SFC/4000)</name>
  <MultiGeometry>
    <Polygon>
      <altitudeMode>absolute</altitudeMode>
      <outerBoundaryIs><LinearRing><coordinates>
        131.042777777778,31.8522222222222,1219.2
        131.214166666667,31.7727777777778,1219.2
        131.1486,31.58806,1219.2
        130.975555555556,31.8405555555556,1219.2
        131.042777777778,31.8522222222222,1219.2
      </coordinates></LinearRing></outerBoundaryIs>
    </Polygon>
    <Polygon>
      <altitudeMode>absolute</altitudeMode>
      <outerBoundaryIs><LinearRing><coordinates>
        131.042777777778,31.8522222222222,0
        131.214166666667,31.7727777777778,0
        131.214166666667,31.7727777777778,1219.2
        131.042777777778,31.8522222222222,1219.2
        131.042777777778,31.8522222222222,0
      </coordinates></LinearRing></outerBoundaryIs>
    </Polygon>
  </MultiGeometry>
</Placemark>
</Document>
</kml>"""


def test_kml_and_kmz_import_without_automatic_leg_conversion() -> None:
    plain = import_kml_or_kmz(KML, filename="route.kml")
    assert len(plain.points) == 1
    assert len(plain.lines) == 1
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w") as archive:
        archive.writestr("doc.kml", KML)
    kmz = import_kml_or_kmz(archive_bytes.getvalue(), filename="route.kmz")
    assert kmz.points == plain.points


def test_kmz_rejects_path_traversal() -> None:
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w") as archive:
        archive.writestr("../route.kml", KML)
    with pytest.raises(KmlImportError, match="unsafe path"):
        import_kml_or_kmz(archive_bytes.getvalue(), filename="route.kmz")


def test_pasted_google_earth_multigeometry_keeps_only_horizontal_polygon() -> None:
    imported = import_kml_text(GOOGLE_EARTH_3D_POLYGON)

    assert imported.source_files == ("pasted.kml",)
    assert imported.points == ()
    assert imported.lines == ()
    assert len(imported.polygons) == 1
    polygon = imported.polygons[0]
    assert polygon.name == "KS4-6(SFC/4000)"
    assert polygon.altitude_mode == "absolute"
    assert polygon.minimum_altitude_m == pytest.approx(1219.2)
    assert polygon.maximum_altitude_m == pytest.approx(1219.2)
    assert polygon.outer_boundary[0] == polygon.outer_boundary[-1]
    assert len(polygon.outer_boundary) == 5
    assert imported.warnings == (
        "KS4-6(SFC/4000): skipped 1 Polygon surface(s) without a usable horizontal boundary",
    )


def test_polygon_inner_boundary_is_preserved_and_closed() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>
        <name>AREA</name><Polygon>
        <outerBoundaryIs><LinearRing><coordinates>
        130,31 131,31 131,32 130,32
        </coordinates></LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing><coordinates>
        130.2,31.2 130.8,31.2 130.8,31.8 130.2,31.8
        </coordinates></LinearRing></innerBoundaryIs>
        </Polygon></Placemark></kml>"""
    )

    polygon = imported.polygons[0]
    assert polygon.outer_boundary[0] == polygon.outer_boundary[-1]
    assert polygon.inner_boundaries[0][0] == polygon.inner_boundaries[0][-1]


def test_polygon_coordinates_count_toward_import_limit_even_when_surface_is_skipped() -> None:
    with pytest.raises(KmlImportError, match="coordinate limit"):
        import_kml_text(
            GOOGLE_EARTH_3D_POLYGON,
            limits=ImportLimits(max_coordinates=8),
        )


@pytest.mark.parametrize(
    "text",
    [
        "not XML",
        "<Document><Placemark /></Document>",
        """<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <kml><Placemark><name>&xxe;</name></Placemark></kml>""",
    ],
)
def test_pasted_kml_rejects_non_kml_or_unsafe_xml(text: str) -> None:
    with pytest.raises(KmlImportError):
        import_kml_text(text)


def test_atomic_project_save_and_revision_conflict(tmp_path, project) -> None:
    repository = LocalProjectRepository(tmp_path)
    saved = repository.save(project, expected_revision=0)
    assert saved.project.revision == 1
    loaded = repository.load(project.id)
    assert loaded == saved.project
    with pytest.raises(RevisionConflictError) as captured:
        repository.save(project, expected_revision=0)
    assert captured.value.conflict_copy.exists()

    with pytest.raises(RevisionConflictError) as second:
        repository.save(project, expected_revision=0)

    assert second.value.conflict_copy.exists()
    assert second.value.conflict_copy != captured.value.conflict_copy
    assert len(captured.value.conflict_copy.stem.rsplit("-", 1)[-1]) == 32
    assert repository.load(project.id) == saved.project
