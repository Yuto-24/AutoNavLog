import importlib.util
import json
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


def test_ci_checks_push_format_without_comparing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validator, "git", lambda *_: pytest.fail("push must not inspect git"))
    validator.validate(ROOT)


@pytest.mark.parametrize(
    ("path", "patch", "requires"),
    [
        ("web/src/styles.css", "-#header {color: red}\n+#header {color: blue}", True),
        ("src/autonavlog/app.py", "-# old\n+# new", False),
        ("src/autonavlog/app.py", "-value = 1\n+value = 2", True),
        ("web/src/App.tsx", "-// old\n+// new", False),
        ("web/src/App.tsx", "-<h3>旧見出し</h3>\n+<h3>新見出し</h3>", False),
        ("web/src/App.tsx", '-<button title="旧" />\n+<button title="新" />', False),
        (
            "web/src/App.tsx",
            "-<button onClick={old}>旧</button>\n+<button onClick={new}>新</button>",
            True,
        ),
        ("web/src/styles.css", "-* {color: red}\n+* {color: blue}", True),
        ("data/config.json", '-{"value":1}\n+{"value":2}', True),
    ],
)
def test_clear_runtime_diffs(path: str, patch: str, requires: bool) -> None:
    assert validator.runtime_path(path)
    assert validator.meaningful_diff(patch, path) is requires


@pytest.mark.parametrize(
    "path", ["README.md", "docs/setup.md", "tests/unit/test_x.py", "KNOWN_ISSUES.md"]
)
def test_nonrelease_paths_are_exempt(path: str) -> None:
    assert not validator.runtime_path(path)


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("has_fragment", [False, True])
def test_pr_runtime_changes_require_fragment_or_fully_consumed_release(
    monkeypatch: pytest.MonkeyPatch,
    released: bool,
    has_fragment: bool,
) -> None:
    monkeypatch.setattr(validator, "read_sources", lambda _: {})
    monkeypatch.setattr(validator, "validate_sources", lambda _: "2.0.0" if released else "1.0.0")
    monkeypatch.setattr(
        validator, "fragment_paths", lambda _: [ROOT / "changes/README.md"] if has_fragment else []
    )
    monkeypatch.setattr(validator, "parse_fragment", lambda _: {})

    def fake_git(_, *args):
        if args[0] == "merge-base":
            return "mergebase"
        if args[0] == "show":
            return json.dumps({"version": "1.0.0"})
        if "--diff-filter=A" in args:
            return "changes/1-change.md" if has_fragment else ""
        if "--name-only" in args:
            return "web/src/App.tsx"
        return "-const value = 1\n+const value = 2"

    monkeypatch.setattr(validator, "git", fake_git)
    if released == has_fragment:
        with pytest.raises(ValueError):
            validator.validate(ROOT, "base")
    else:
        validator.validate(ROOT, "base")


@pytest.mark.parametrize(
    "patch",
    [
        "-<button aria-label='保存' />\n+<button aria-label='保存する' />",
        '-<button aria-label={busy ? "保存中" : "保存"} />\n'
        '+<button aria-label={busy ? "処理中" : "保存"} />',
        " <button>\n-以前の表示\n+新しい表示\n </button>",
    ],
)
def test_jsx_copy_exemptions_include_quotes_conditions_and_multiline(patch: str) -> None:
    assert not validator.meaningful_diff(patch, "web/src/App.tsx")


@pytest.mark.parametrize(
    "patch",
    [
        '-const title = "old";\n+const title = "new";',
        '-fetch("/old");\n+fetch("/new");',
        '-<button aria-label={busy ? "保存中" : "保存"} />\n'
        '+<button aria-label={other ? "保存中" : "保存"} />',
        '-<button title={lookup("old")} />\n+<button title={lookup("new")} />',
    ],
)
def test_copy_exemptions_do_not_hide_code_or_unknown_expression_changes(patch: str) -> None:
    assert validator.meaningful_diff(patch, "web/src/App.tsx")
