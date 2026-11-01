import importlib.util
import sys
from pathlib import Path

import pytest

NOTES = "\n## Pending release notes\nNone: release-policy change only.\n"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "validate_release", ROOT / "scripts/validate_release.py"
)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


@pytest.fixture
def advanced_base_repo(tmp_path):
    def write_release(version):
        (tmp_path / "VERSION").write_text(version + "\n")
        (tmp_path / "CHANGELOG.md").write_text(
            f"## {version}\n\n### 利用者向け\n\n#### 修正\n\n- 修正\n\n"
            "### 開発者向け\n\n- Issue #1\n"
        )

    def commit(message):
        validator.git(tmp_path, "add", ".")
        validator.git(tmp_path, "commit", "-m", message)
        return validator.git(tmp_path, "rev-parse", "HEAD").strip()

    validator.git(tmp_path, "init", "-b", "main")
    validator.git(tmp_path, "config", "user.name", "Release test")
    validator.git(tmp_path, "config", "user.email", "release@example.test")
    validator.git(tmp_path, "config", "commit.gpgsign", "false")
    write_release("1.0.0")
    stale_base = commit("old event base")
    write_release("1.1.0")
    actual_base = commit("main release advances")
    validator.git(tmp_path, "checkout", "-b", "pr")
    return tmp_path, stale_base, actual_base, write_release, commit


@pytest.mark.parametrize("pr_change", ["docs", "version", "changelog"])
def test_verified_merge_uses_actual_base_without_hiding_pr_release_changes(
    advanced_base_repo, pr_change,
):
    root, stale_base, actual_base, write_release, commit = advanced_base_repo
    (root / "README.md").write_text("PR documentation\n")
    if pr_change == "version":
        write_release("1.2.0")
    elif pr_change == "changelog":
        with (root / "CHANGELOG.md").open("a") as stream:
            stream.write("- Another developer note\n")
    pr_head = commit("PR change")
    validator.git(root, "checkout", "main")
    validator.git(root, "merge", "--no-ff", "pr", "-m", "synthetic PR merge")
    assert validator.checked_pr_base(root, pr_head) == actual_base
    body = "Release: not-required\nReason: normal PR" + NOTES
    # Reproduce the stale event's false VERSION finding before selecting the
    # verified first parent. Real PR VERSION/CHANGELOG edits must still fail.
    with pytest.raises(ValueError, match="must not change VERSION"):
        validator.validate(root, stale_base, body)
    if pr_change == "docs":
        validator.validate(root, stale_base, body, pr_head)
    else:
        expected = "VERSION" if pr_change == "version" else "CHANGELOG"
        with pytest.raises(ValueError, match=f"must not change {expected}"):
            validator.validate(root, stale_base, body, pr_head)
    if pr_change == "version":
        validator.validate(root, stale_base, "Release: required", pr_head)


