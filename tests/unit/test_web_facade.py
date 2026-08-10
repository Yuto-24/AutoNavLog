from __future__ import annotations

import pytest

from autonavlog.web.facade import AutoNavLogWebApplication


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        (".", "route"),
        ("..", "route"),
        ("route. ", "route"),
        ("a" * 59 + ".x", "a" * 59),
        ("valid-name", "valid-name"),
    ],
)
def test_project_name_normalization_never_leaves_a_reserved_dot_suffix(
    raw_name: str,
    expected: str,
) -> None:
    assert AutoNavLogWebApplication._normalize_project_name(raw_name) == expected
