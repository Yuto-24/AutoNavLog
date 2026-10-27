from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_scope():
    for name in ("test", "publish-github-release"):
        source = (ROOT / f".github/workflows/{name}.yml").read_text()
        assert "branches: [main, release/1.x]" in source
    source = (ROOT / ".github/workflows/publish-github-release.yml").read_text()
    assert "fetch-depth: 0" in source
    assert "cancel-in-progress: false" in source
    assert "queue: max" in source
    assert "python3 scripts/publish_github_release.py" in source
    assert "workflow_dispatch:" not in source
