import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "validate_release", ROOT / "scripts/validate_release.py"
)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def test_push_checks_sources_without_git(monkeypatch):
    monkeypatch.setattr(validator, "git", lambda *_: pytest.fail("push must not inspect git"))
    validator.validate(ROOT)


def test_contract_requires_exact_marker_and_reason():
    assert validator.parse_contract("Release: required\n") == "required"
    assert validator.parse_contract("Release: not-required\nReason: docs only\n") == "not-required"
    for body in (
        "",
        "Release: required\nRelease: not-required\nReason: x",
        "Release: not-required",
    ):
        with pytest.raises(ValueError):
            validator.parse_contract(body)


def test_not_required_rejects_runtime(monkeypatch):
    monkeypatch.setattr(validator, "validate_sources", lambda _: "1.0.0")
    monkeypatch.setattr(
        validator, "git", lambda _, *args: "base" if args[0] == "merge-base" else "web/src/App.tsx"
    )
    monkeypatch.setattr(validator, "base_version", lambda *_: "1.0.0")
    with pytest.raises(ValueError, match="production/runtime"):
        validator.validate(ROOT, "base", "Release: not-required\nReason: docs")


def test_required_needs_newer_changed_version_and_changelog(monkeypatch):
    monkeypatch.setattr(validator, "validate_sources", lambda _: "1.1.0")
    monkeypatch.setattr(validator, "base_version", lambda *_: "1.0.0")
    monkeypatch.setattr(
        validator,
        "git",
        lambda _, *args: "base" if args[0] == "merge-base" else "VERSION\nCHANGELOG.md",
    )
    validator.validate(ROOT, "base", "Release: required")


@pytest.mark.parametrize(
    ("current", "changed", "contract", "error"),
    [
        ("1.0.0", "VERSION\nCHANGELOG.md", "required", "newer than base"),
        ("0.9.0", "VERSION\nCHANGELOG.md", "required", "newer than base"),
        ("1.1.0", "VERSION", "required", "change VERSION and CHANGELOG"),
        ("1.1.0", "CHANGELOG.md", "required", "change VERSION and CHANGELOG"),
        ("1.1.0", "VERSION", "not-required", "must not change VERSION"),
        ("1.0.0", "VERSION", "not-required", "must not change VERSION"),
    ],
)
def test_contract_rejects_inconsistent_release(monkeypatch, current, changed, contract, error):
    monkeypatch.setattr(validator, "validate_sources", lambda _: current)
    monkeypatch.setattr(validator, "base_version", lambda *_: "1.0.0")
    monkeypatch.setattr(
        validator, "git", lambda _, *args: "ancestor" if args[0] == "merge-base" else changed
    )
    with pytest.raises(ValueError, match=error):
        validator.validate(ROOT, "base", f"Release: {contract}\nReason: repository maintenance")


def test_not_required_accepts_docs_tests_and_artifact_neutral_ci(monkeypatch):
    monkeypatch.setattr(validator, "validate_sources", lambda _: "1.0.0")
    monkeypatch.setattr(validator, "base_version", lambda *_: "1.0.0")
    changed = "README.md\ntests/unit/test_version.py\n.github/workflows/test.yml\nAGENTS.md"
    monkeypatch.setattr(
        validator, "git", lambda _, *args: "ancestor" if args[0] == "merge-base" else changed
    )
    validator.validate(ROOT, "base", "Release: not-required\nReason: repository maintenance")


@pytest.mark.parametrize(
    "path",
    [
        "src/autonavlog/web/app.py",
        "web/src/App.tsx",
        "web/public/icon.svg",
        "web/index.html",
        "data/performance/cruise.csv",
        "vendor/new.whl",
        "compose.wsl.yaml",
        "Dockerfile",
        "web/package-lock.json",
        "web/vite.config.ts",
    ],
)
def test_definite_runtime_paths(path):
    assert validator.runtime_path(path)


def test_compares_version_to_current_base_not_common_ancestor(monkeypatch):
    monkeypatch.setattr(validator, "validate_sources", lambda _: "1.1.0")
    monkeypatch.setattr(
        validator,
        "git",
        lambda _, *args: "ancestor" if args[0] == "merge-base" else "VERSION\nCHANGELOG.md",
    )
    monkeypatch.setattr(
        validator, "base_version", lambda _, ref: "1.1.0" if ref == "base" else "1.0.0"
    )
    with pytest.raises(ValueError, match="newer than base"):
        validator.validate(ROOT, "base", "Release: required")


def test_base_version_uses_legacy_only_when_version_file_is_absent(monkeypatch):
    def fake_git(_, *args):
        if args[0] == "ls-tree":
            return ""
        assert args == ("show", "base:pyproject.toml")
        return '[project]\nversion = "1.11.1"\n'

    monkeypatch.setattr(validator, "git", fake_git)
    assert validator.base_version(ROOT, "base") == "1.11.1"
    for source in ("1.12.0\n", "invalid\n", "1.12.0\n\n"):

        def with_version(_, *args, source=source):
            if args[0] == "ls-tree":
                return "VERSION\n"
            assert args == ("show", "base:VERSION")
            return source

        monkeypatch.setattr(validator, "git", with_version)
        if source == "1.12.0\n":
            assert validator.base_version(ROOT, "base") == "1.12.0"
        else:
            with pytest.raises(ValueError, match="VERSION"):
                validator.base_version(ROOT, "base")


def test_template_comment_is_not_a_release_contract():
    with pytest.raises(ValueError):
        validator.parse_contract("<!--\nRelease: required\n-->")
    with pytest.raises(ValueError, match="Reason"):
        validator.parse_contract("Release: not-required\n<!--\nReason: example\n-->")


def test_changelog_rejects_invalid_structures_and_version_sources(tmp_path):
    source = "## 1.0.0\n\n### 利用者向け\n\n#### 追加\n\n- 新機能\n\n### 開発者向け\n\n- Issue #1\n"
    (tmp_path / "VERSION").write_text("1.0.0\n")
    (tmp_path / "CHANGELOG.md").write_text(source)
    assert validator.validate_sources(tmp_path) == "1.0.0"
    for invalid in [
        source.replace("## 1.0.0", "## 1.0.0 - 2026-09-10"),
        source + source,
        source.replace("#### 追加", "#### 修正\n- 修正\n#### 追加"),
        source.replace("#### 追加", "#### 追加\n#### 改善"),
        source.replace("- 新機能", "- Issue #2"),
        source.replace("- 新機能", ""),
        source.replace("- Issue #1", ""),
        source.replace("- 新機能", "# title\n- 新機能"),
        source.replace("## 1.0.0", "## Unreleased"),
        "### rogue\n" + source,
    ]:
        (tmp_path / "CHANGELOG.md").write_text(invalid)
        with pytest.raises(ValueError):
            validator.validate_sources(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(source)
    for value in ("1.0.1\n", " 1.0.0\n", "1.0.0\n\n", "v1.0.0\n"):
        (tmp_path / "VERSION").write_text(value)
        with pytest.raises(ValueError):
            validator.validate_sources(tmp_path)


@pytest.mark.parametrize(
    "after",
    [
        '# Updated comment\n"""Module documentation."""\nx = 1\n',
        "x = 1\n",
    ],
)
def test_python_comment_and_docstring_only_changes_are_exempt(monkeypatch, after):
    before = '# Original comment\n"""Old module documentation."""\nx = 1\n'
    monkeypatch.setattr(
        validator, "git", lambda _, *args: after if args[-1].startswith("HEAD:") else before
    )
    assert validator.python_documentation_only(ROOT, "base", "src/module.py")


@pytest.mark.parametrize(
    "after, exempt",
    [
        ('def f():\n    """New docstring."""\n    return 1\n', True),
        ('def f():\n    """Old docstring."""\n    return 2\n', False),
        ("invalid python {", False),
    ],
)
def test_python_docstring_exemption_preserves_code_changes(monkeypatch, after, exempt):
    before = 'def f():\n    """Old docstring."""\n    return 1\n'
    monkeypatch.setattr(
        validator, "git", lambda _, *args: after if args[-1].startswith("HEAD:") else before
    )
    assert validator.python_documentation_only(ROOT, "base", "src/module.py") is exempt


def test_generator_test_files_are_not_runtime():
    assert not validator.runtime_path("web/scripts/generate-release-notes.test.mjs")
    assert validator.runtime_path("web/scripts/generate-release-notes.mjs")
