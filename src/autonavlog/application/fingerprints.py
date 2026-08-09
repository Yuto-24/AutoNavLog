from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any
from uuid import UUID

FINGERPRINT_VERSION = 1
_MAX_DEPTH = 32
_MAX_SAFE_INT = 2**53 - 1


def normalize(value: Any, *, _depth: int = 0) -> Any:
    """Normalize a supported value into the canonical fingerprint payload form."""

    if _depth > _MAX_DEPTH:
        raise ValueError("fingerprint payload nested too deeply")
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INT:
            raise ValueError("integer out of safe range")
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN/Infinity is not fingerprintable")
        rounded = round(value, 6) + 0.0
        return f"{rounded:.6f}"
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Enum):
        return normalize(value.value, _depth=_depth + 1)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        try:
            offset = value.utcoffset()
        except (NotImplementedError, TypeError) as error:
            raise ValueError("datetime must be timezone-aware") from error
        if value.tzinfo is None or offset is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        raise ValueError("naked time is not fingerprintable; use datetime")
    if isinstance(value, (list, tuple)):
        return [normalize(item, _depth=_depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        members = [normalize(item, _depth=_depth + 1) for item in value]
        return sorted(
            members,
            key=lambda member: json.dumps(
                member,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, bool) or not isinstance(
                key,
                (str, int, UUID, Enum),
            ):
                raise ValueError(f"unsupported fingerprint key type: {type(key)!r}")
            normalized_key = normalize(key, _depth=_depth + 1)
            text_key = normalized_key if isinstance(normalized_key, str) else str(normalized_key)
            if text_key in output:
                raise ValueError(f"duplicate key after normalization: {text_key}")
            output[text_key] = normalize(item, _depth=_depth + 1)
        return output
    raise TypeError(f"unsupported fingerprint value type: {type(value)!r}")


def canonical_json(*, kind: str, fields: dict[str, Any]) -> str:
    canonical = normalize(
        {
            "fingerprint_version": FINGERPRINT_VERSION,
            "kind": kind,
            "fields": fields,
        }
    )
    return json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def make_fingerprint(*, kind: str, fields: dict[str, Any]) -> str:
    payload = canonical_json(kind=kind, fields=fields)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
