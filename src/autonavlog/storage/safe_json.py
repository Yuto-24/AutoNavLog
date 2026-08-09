from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

JsonModelT = TypeVar("JsonModelT", bound=BaseModel)


class JsonStorageError(ValueError):
    """Raised when persisted JSON cannot be trusted or validated."""


def _reject_constant(value: str) -> Any:
    raise JsonStorageError(f"non-finite JSON constant is not allowed: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise JsonStorageError(f"non-finite JSON number is not allowed: {value}")
    return parsed


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise JsonStorageError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def validate_json_bytes(
    raw: bytes,
    model: type[JsonModelT],
) -> JsonModelT:
    """Validate syntax and the Pydantic model against the exact same bytes."""

    try:
        text = raw.decode("utf-8", errors="strict")
        json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        return model.model_validate_json(raw, strict=True)
    except (UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise JsonStorageError(f"invalid persisted {model.__name__} JSON") from error


def read_json_model(path: Path, model: type[JsonModelT]) -> JsonModelT:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise JsonStorageError(f"cannot read JSON file: {path}") from error
    return validate_json_bytes(raw, model)


def _ensure_finite_model_values(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonStorageError("model contains a non-finite value")
        return
    if isinstance(value, dict):
        for item in value.values():
            _ensure_finite_model_values(item)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _ensure_finite_model_values(item)


def model_json_bytes(instance: BaseModel) -> bytes:
    try:
        _ensure_finite_model_values(instance.model_dump(mode="python"))
        serialized = json.dumps(
            instance.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise JsonStorageError("model contains a non-JSON or non-finite value") from error
    return (serialized + "\n").encode("utf-8")


def _write_temp(path: Path, raw: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _replace_bytes(path: Path, raw: bytes) -> None:
    temporary = _write_temp(path, raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_validated_write(
    path: Path,
    raw: bytes,
    validator: Callable[[bytes], object],
    *,
    keep_backup: bool = True,
) -> None:
    """Publish validated bytes atomically, restoring the previous file on failure."""

    validator(raw)
    previous = path.read_bytes() if path.exists() else None
    temporary = _write_temp(path, raw)
    backup = path.with_name(f"{path.name}.bak")
    try:
        temporary_raw = temporary.read_bytes()
        if temporary_raw != raw:
            raise JsonStorageError("temporary JSON read-back differs from input")
        validator(temporary_raw)
        if keep_backup and previous is not None:
            _replace_bytes(backup, previous)
        os.replace(temporary, path)
        published = path.read_bytes()
        if published != raw:
            raise JsonStorageError("published JSON read-back differs from input")
        validator(published)
    except Exception:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            _replace_bytes(path, previous)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def atomic_model_write(
    path: Path,
    instance: JsonModelT,
    *,
    keep_backup: bool = True,
) -> None:
    raw = model_json_bytes(instance)
    model = type(instance)
    atomic_validated_write(
        path,
        raw,
        lambda candidate: validate_json_bytes(candidate, model),
        keep_backup=keep_backup,
    )
