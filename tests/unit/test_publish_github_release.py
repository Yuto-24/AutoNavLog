"""Release routing and immutable-tag publication, without network access."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_github_release as publisher  # noqa: E402


@pytest.mark.parametrize(
    "ref,version,expected",
    [
        ("refs/heads/main", "2.1.0", ("NavMate", "true")),
        ("refs/heads/release/1.x", "1.23.0", ("AutoNavLog", "false")),
    ],
)
def test_release_line(ref, version, expected):
    assert publisher.release_line(ref, version) == expected


@pytest.mark.parametrize(
    "ref,version",
    [
        ("refs/heads/main", "1.23.0"),
        ("refs/heads/main", "3.0.0"),
        ("refs/heads/release/1.x", "2.0.0"),
        ("refs/heads/dev", "2.0.0"),
        ("refs/tags/v2.0.0", "2.0.0"),
        ("refs/heads/main", "2.0.0-rc.1"),
    ],
)
def test_reject_wrong_line(ref, version):
    with pytest.raises(ValueError):
        publisher.release_line(ref, version)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    version = "2.1.0"
    notes = "### 利用者向け\n\n#### 修正\n\n- Fix\n\n### 開発者向け\n\n- Tests\n"
    changelog = f"# Changes\n\n## {version}\n\n{notes}\n## 1.0.0\n\n{notes}"
    (tmp_path / "VERSION").write_text(version + "\n")
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    state = SimpleNamespace(
        tag=None,
        release=None,
        calls=[],
        ancestor=True,
        tagged_version=version,
        tagged_changelog=changelog,
        head="head",
    )

    def api(path):
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": state.tag}} if state.tag else None
        assert "/releases/tags/" in path
        return state.release

    def run(*args):
        state.calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return state.head
        if args[:2] == ("git", "merge-base"):
            if not state.ancestor:
                raise subprocess.CalledProcessError(1, args)
            return ""
        if args[:2] == ("git", "show"):
            return state.tagged_version if args[-1].endswith(":VERSION") else state.tagged_changelog
        assert args[:3] == ("gh", "release", "create")
        state.notes = Path(args[args.index("--notes-file") + 1]).read_text()
        return "url"

    monkeypatch.setattr(publisher, "api", api)
    monkeypatch.setattr(publisher, "run", run)
    state.publish = lambda: publisher.publish(tmp_path, "refs/heads/main", "head", "owner/repo")
    state.root = tmp_path
    return state


def test_publish_new_release_with_exact_notes_and_sha(scenario):
    scenario.publish()
    command = scenario.calls[-1]
    assert command[:4] == ("gh", "release", "create", "v2.1.0")
    assert command[command.index("--target") + 1] == "head"
    assert command[command.index("--title") + 1] == "NavMate 2.1.0"
    assert "--latest=true" in command
    assert "## 1.0.0" not in scenario.notes
    assert scenario.notes.startswith("### 利用者向け")


def test_v1_never_becomes_latest(scenario):
    for path in ("VERSION", "CHANGELOG.md"):
        file = scenario.root / path
        file.write_text(file.read_text().replace("2.1.0", "1.23.0"))
    publisher.publish(scenario.root, "refs/heads/release/1.x", "head", "owner/repo")
    assert "--latest=false" in scenario.calls[-1]
    assert "AutoNavLog 1.23.0" in scenario.calls[-1]


def test_existing_release_checks_tag_and_allows_later_maintenance(scenario):
    scenario.tag = "ancestor"
    scenario.release = {"draft": False, "prerelease": False}
    scenario.publish()
    assert ("git", "merge-base", "--is-ancestor", "ancestor", "head") in scenario.calls
    assert not any(call[0] == "gh" for call in scenario.calls)


def test_existing_tag_without_release_is_preserved(scenario):
    scenario.tag = "ancestor"
    scenario.publish()
    assert "--verify-tag" in scenario.calls[-1]
    assert scenario.calls[-1][scenario.calls[-1].index("--target") + 1] == "ancestor"


@pytest.mark.parametrize(
    "failure", ["foreign", "version", "notes", "missing", "draft", "prerelease", "head"]
)
def test_conflicting_existing_release_fails_without_mutation(scenario, failure):
    scenario.tag = "ancestor"
    scenario.release = {"draft": False, "prerelease": False}
    if failure == "foreign":
        scenario.ancestor = False
    elif failure == "version":
        scenario.tagged_version = "2.0.0"
    elif failure == "notes":
        scenario.tagged_changelog = scenario.tagged_changelog.replace("- Fix", "- Different")
    elif failure == "missing":
        scenario.tag = None
    elif failure == "head":
        scenario.head = "different"
    else:
        scenario.release[failure] = True
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        scenario.publish()
    assert not any(call[0] == "gh" for call in scenario.calls)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_api_errors_are_not_missing_releases(monkeypatch, status):
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stderr=f"gh: error (HTTP {status})", stdout=""
        ),
    )
    with pytest.raises(RuntimeError):
        publisher.api("repos/owner/repo/releases/tags/v2.0.0")


def test_api_explicit_404(monkeypatch):
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stderr="gh: Not Found (HTTP 404)", stdout=""),
    )
    assert publisher.api("missing") is None


def test_api_success(monkeypatch):
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"id": 1})),
    )
    assert publisher.api("release") == {"id": 1}


def test_peel_annotated_tag(monkeypatch):
    values = iter(
        [
            {"object": {"type": "tag", "sha": "tag-object"}},
            {"object": {"type": "commit", "sha": "commit"}},
        ]
    )
    monkeypatch.setattr(publisher, "api", lambda _: next(values))
    assert publisher.tag_commit("owner/repo", "v2.0.0") == "commit"

