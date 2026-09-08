import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "prepare_release", ROOT / "scripts/prepare_release.py"
)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
from release_content import (  # noqa: E402
    SOURCE_PATHS,
    histories,
    parse_fragment,
    parse_known_issues,
    read_sources,
    validate_sources,
)

FRAGMENT = """<!-- section: 修正 -->
<!-- issue: 999 -->
<!-- user-visible: true -->

## Developer
- Issue #999で計算条件を修正。

## User
- 保存した計画を開けない問題を修正しました。
"""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    for name in SOURCE_PATHS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes/README.md").write_text("# Guide\n")
    (tmp_path / "changes/999-z.md").write_text(FRAGMENT)
    return tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_check_plans_every_fragment_without_mutation_then_release_consumes_all(
    repository: Path,
) -> None:
    (repository / "changes/999-a.md").write_text(FRAGMENT.replace("計算条件", "先頭の計算条件"))
    before = snapshot(repository)
    release.prepare_release(repository, "99.0.0", check=True)
    assert snapshot(repository) == before
    release.prepare_release(repository, "99.0.0")
    assert list((repository / "changes").iterdir()) == [repository / "changes/README.md"]
    assert validate_sources(read_sources(repository)) == "99.0.0"
    history = (repository / "CHANGELOG.md").read_text()
    assert history.index("先頭の計算条件") < history.index("で計算条件")
    assert " JST" in histories(history, user=False)[0][1]
    user = (repository / "RELEASE_NOTES.md").read_text()
    assert "配布" not in user
    assert "Issue #999" not in user


@pytest.mark.parametrize("version", ["v99.0.0", "01.2.3", "1.2", "1.2.3-beta", "0.0.0", "1.0.0"])
def test_bad_target_rejected_without_mutation(repository: Path, version: str) -> None:
    before = snapshot(repository)
    with pytest.raises(ValueError):
        release.prepare_release(repository, version)
    assert snapshot(repository) == before


def test_same_version_and_zero_fragments_rejected(repository: Path) -> None:
    current = validate_sources(read_sources(repository))
    with pytest.raises(ValueError):
        release.prepare_release(repository, current)
    (repository / "changes/999-z.md").unlink()
    before = snapshot(repository)
    with pytest.raises(ValueError, match="No change fragments"):
        release.prepare_release(repository, "99.0.0")
    assert snapshot(repository) == before


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("corruption", ["fragment", "notes", "known", "lock", "history"])
def test_all_invalid_sources_leave_every_byte_unchanged(
    repository: Path, check: bool, corruption: str
) -> None:
    names = {
        "fragment": "changes/999-z.md",
        "notes": "RELEASE_NOTES.md",
        "known": "KNOWN_ISSUES.md",
        "lock": "web/package-lock.json",
        "history": "CHANGELOG.md",
    }
    path = repository / names[corruption]
    if corruption == "lock":
        document = json.loads(path.read_text())
        document["packages"][""]["version"] = "0.0.0"
        path.write_text(json.dumps(document))
    elif corruption == "history":
        path.write_text(path.read_text().replace("## ", "## 99.0.0 - 2026-01-01\n\n## ", 1))
    else:
        path.write_text(path.read_text() + "\n### Unexpected\n- invalid\n")
    before = snapshot(repository)
    with pytest.raises(ValueError):
        release.prepare_release(repository, "99.0.0", check=check)
    assert snapshot(repository) == before


def test_developer_only_release_gets_plain_user_fallback(repository: Path) -> None:
    (repository / "changes/999-z.md").write_text(
        FRAGMENT.split("## User")[0].replace("true", "false")
    )
    release.prepare_release(repository, "99.0.0")
    assert release.FALLBACK in (repository / "RELEASE_NOTES.md").read_text()


def test_write_failure_rolls_back_outputs_and_fragments(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(repository)
    original = Path.write_text

    def fail_one(path: Path, *args, **kwargs):
        if path.name == "package.json":
            raise OSError("simulated write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_one)
    with pytest.raises(OSError, match="simulated"):
        release.prepare_release(repository, "99.0.0")
    assert snapshot(repository) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("section: 修正", "section: 配布"),
        lambda text: text.replace("issue: 999", "unknown: 999"),
        lambda text: text.replace("<!-- issue: 999 -->\n", ""),
        lambda text: text.replace("true", "maybe"),
        lambda text: text.replace("- 保存", "保存"),
        lambda text: text + "\n## User\n- duplicate\n",
    ],
)
def test_fragment_format_errors(change) -> None:
    with pytest.raises(ValueError):
        parse_fragment(change(FRAGMENT))


def test_known_issue_optional_sections_and_metadata_validation() -> None:
    issue = (
        "<!-- id: saved-plan -->\n<!-- github-issue: 999 -->\n"
        "## 保存した計画\n開けない場合があります。\n"
    )
    assert parse_known_issues(issue)[0]["title"] == "保存した計画"
    assert parse_known_issues(issue + "<!-- 説明用コメント -->\n") == parse_known_issues(issue)
    assert parse_known_issues("# 既知の不具合\n") == []
    for bad in (
        issue + "### 原因\n- x",
        issue + "### 回避方法\n",
        issue + issue,
        issue.replace("github-issue", "severity"),
        "## No ID\nDescription",
        "<!-- id: empty -->\n## Title",
        "<!-- id: empty -->",
    ):
        with pytest.raises(ValueError):
            parse_known_issues(bad)


def test_release_history_may_start_at_its_first_version_header(repository: Path) -> None:
    for name in ("CHANGELOG.md", "RELEASE_NOTES.md"):
        path = repository / name
        source = path.read_text()
        path.write_text(source[source.index("## ") :])
    release.prepare_release(repository, "99.0.0")
    assert validate_sources(read_sources(repository)) == "99.0.0"
