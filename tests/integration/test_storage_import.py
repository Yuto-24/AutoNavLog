from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from autonavlog.importers.kml import KmlImportError, import_kml_or_kmz
from autonavlog.storage.local import LocalProjectRepository, RevisionConflictError

KML = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>TP1</name><Point><coordinates>131.5,32.0,100</coordinates></Point></Placemark>
<Placemark><name>Reference</name><LineString><coordinates>
131.5,32.0 131.6,32.1 131.7,32.2
</coordinates></LineString></Placemark>
</Document></kml>"""


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


def test_atomic_project_save_and_revision_conflict(tmp_path, project) -> None:
    repository = LocalProjectRepository(tmp_path)
    saved = repository.save(project, expected_revision=0)
    assert saved.project.revision == 1
    loaded = repository.load(project.id)
    assert loaded == saved.project
    with pytest.raises(RevisionConflictError) as captured:
        repository.save(project, expected_revision=0)
    assert captured.value.conflict_copy.exists()
    assert repository.load(project.id) == saved.project
