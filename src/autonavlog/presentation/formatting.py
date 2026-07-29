from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from autonavlog.domain.values import AdoptedValue
from autonavlog.nav.rounding import DisplayRoundingPolicy

ROUNDING = DisplayRoundingPolicy()
JST = ZoneInfo("Asia/Tokyo")


def format_duration(seconds: float) -> str:
    minutes = ROUNDING.duration_minutes(seconds)
    return f"{minutes:.1f} min"


def format_clock(value: datetime) -> str:
    return value.astimezone(JST).strftime("%H:%M")


def format_adopted(
    value: AdoptedValue[Any],
    formatter: Callable[[Any], str] = str,
) -> str:
    adopted = value.adopted()
    return "未確定" if adopted is None else formatter(adopted)


def provenance_details(label: str, value: AdoptedValue[Any]) -> str:
    adopted = value.adopted()
    state = value.state.value
    automatic = value.automatic_value
    manual = value.manual_override
    warnings = ", ".join(value.warnings) or "なし"
    metadata = escape(str(value.automatic_metadata))
    return (
        f"<details><summary>{escape(label)} <span class='state'>{state}</span></summary>"
        f"<dl><dt>採用値</dt><dd>{escape(str(adopted))}</dd>"
        f"<dt>自動値</dt><dd>{escape(str(automatic))}</dd>"
        f"<dt>手動値</dt><dd>{escape(str(manual))}</dd>"
        f"<dt>Warning</dt><dd>{escape(warnings)}</dd>"
        f"<dt>根拠</dt><dd><code>{metadata}</code></dd></dl></details>"
    )
