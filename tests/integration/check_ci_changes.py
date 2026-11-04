"""Exercise CI routing against real Git history, without application dependencies."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_changes  # noqa: E402


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, changes):
    for name, content in changes.items():
        path = root / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "CI fixture")
    git(tmp_path, "config", "user.email", "ci@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    base = commit(
        tmp_path,
        {
            "README.md": "Readme\n",
            "AGENTS.md": "Guide\n",
            "docs/guide.md": "Docs\n",
            "src/app.py": "print('app')\n",
        },
    )
    return tmp_path, base


def pr(base, head):
    return {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}


@pytest.mark.parametrize(
    "changes,run_full",
    [
        ({"README.md": "Updated readme\n"}, False),
        ({"AGENTS.md": "Updated instructions\n"}, False),
        ({"docs/nested/guide.md": "New docs\n", "docs/guide.md": None}, False),
        ({"docs/guide.md": "Docs\n", "src/app.py": "print('changed')\n"}, True),
        ({"CHANGELOG.md": "Release notes\n"}, True),
        ({"KNOWN_ISSUES.md": "Issue text for the UI\n"}, True),
        ({"VERSION": "1.0.0\n"}, True),
        ({"web/public/help.md": "Public artifact\n"}, True),
        ({"docs/static_quota.example.json": "{}"}, True),
        ({"docs/reference/data.xlsx": "fixture"}, True),
        ({".github/workflows/test.yml": "jobs: {}\n"}, True),
        ({"scripts/ci_changes.py": "# Updated classifier\n"}, True),
        ({"tests/integration/check_ci_changes.py": "# Updated tests\n"}, True),
        ({"unexpected-file": "unknown scope\n"}, True),
        ({"README.md": None}, True),
        ({"src/app.py": None}, True),
    ],
)
def test_scope_allowlist_and_runtime_inputs(repo, changes, run_full):
    root, base = repo
    head = commit(root, changes)
    assert ci_changes.ci_scope(root, "pull_request", pr(base, head))[0] is run_full


def test_pr_uses_merge_base_and_all_commits_on_the_pr(repo):
    root, base = repo
    git(root, "checkout", "--quiet", "-b", "docs")
    head = commit(root, {"docs/guide.md": "Updated docs\n"})
    git(root, "checkout", "--quiet", "main")
    advanced_base = commit(root, {"src/app.py": "print('base moved')\n"})
    assert ci_changes.ci_scope(root, "pull_request", pr(advanced_base, head))[0] is False

    git(root, "checkout", "--quiet", "docs")
    commit(root, {"src/app.py": "print('PR code')\n"})
    head = commit(root, {"AGENTS.md": "Latest commit is documentation\n"})
    assert ci_changes.ci_scope(root, "pull_request", pr(base, head))[0] is True


def test_push_checks_entire_before_after_range(repo):
    root, base = repo
    docs_head = commit(root, {"docs/guide.md": "Updated docs\n"})
    event = {"before": base, "after": docs_head}
    assert ci_changes.ci_scope(root, "push", event)[0] is False
    commit(root, {"src/app.py": "print('changed')\n"})
    event["after"] = commit(root, {"AGENTS.md": "Latest commit is documentation\n"})
    assert ci_changes.ci_scope(root, "push", event)[0] is True


def test_moving_code_to_docs_does_not_hide_the_deleted_source(repo):
    root, base = repo
    head = commit(root, {"src/app.py": None, "docs/example.md": "print('app')\n"})
    assert ci_changes.ci_scope(root, "pull_request", pr(base, head))[0] is True


@pytest.mark.parametrize("kind", ["symlink", "executable"])
def test_non_regular_documentation_changes_require_full_ci(repo, kind):
    root, base = repo
    if kind == "symlink":
        (root / "README.md").unlink()
        (root / "README.md").symlink_to("docs/guide.md")
    else:
        (root / "docs/guide.md").chmod(0o755)
    head = commit(root, {})
    assert ci_changes.ci_scope(root, "push", {"before": base, "after": head})[0] is True


def test_diff_has_no_file_limit_and_preserves_unusual_paths(repo):
    root, base = repo
    changes = {f"docs/{index:04d}.md": "Documentation\n" for index in range(350)}
    changes["docs/space and\nnewline 日本語.md"] = "More docs\n"
    docs_head = commit(root, changes)
    assert len(ci_changes.changed_files(root, "pull_request", pr(base, docs_head))) == 351
    assert ci_changes.ci_scope(root, "pull_request", pr(base, docs_head))[0] is False
    head = commit(root, {"src/app.py": "print('changed after many docs')\n"})
    assert ci_changes.ci_scope(root, "pull_request", pr(base, head))[0] is True


@pytest.mark.parametrize(
    "event_name,event",
    [
        ("pull_request", {}),
        ("push", {"before": "0" * 40, "after": "1" * 40}),
        ("push", {"before": "1" * 40, "after": "2" * 40}),
        ("push", {"before": "--output=/tmp/unwanted", "after": "2" * 40}),
        ("push", {"before": None, "after": "2" * 40}),
        ("workflow_dispatch", {}),
    ],
)
def test_unknown_scope_runs_full_ci(repo, event_name, event):
    assert ci_changes.ci_scope(repo[0], event_name, event)[0] is True


def test_empty_diff_runs_full_ci(repo):
    root, base = repo
    assert ci_changes.ci_scope(root, "pull_request", pr(base, base))[0] is True


def test_github_outputs_and_summary(repo, monkeypatch):
    root, base = repo
    head = commit(root, {"docs/guide.md": "Updated docs\n"})
    event_path = root / "event.json"
    output_path = root / "output.txt"
    summary_path = root / "summary.md"
    event_path.write_text(json.dumps(pr(base, head)))
    monkeypatch.setenv("GITHUB_EVENT_PATH", os.fspath(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_OUTPUT", os.fspath(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", os.fspath(summary_path))
    ci_changes.main(root)
    assert output_path.read_text() == "run_full=false\n"
    assert "Documentation-only change (1 files)" in summary_path.read_text()

    event_path.write_text("invalid JSON")
    ci_changes.main(root)
    assert output_path.read_text().splitlines()[-1] == "run_full=true"
