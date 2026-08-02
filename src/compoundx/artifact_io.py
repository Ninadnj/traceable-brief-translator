"""Small, shared helpers for immutable local artifacts."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel


def artifact_paths(
    output_dir: str | Path,
    filenames: Mapping[str, str],
) -> dict[str, Path]:
    """Resolve named artifact paths beneath one output directory."""

    output = Path(output_dir)
    return {
        name: output / filename
        for name, filename in filenames.items()
    }


def existing_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    """Return existing targets without changing the filesystem."""

    return tuple(Path(path) for path in paths if Path(path).exists())


def model_bytes(model: BaseModel) -> bytes:
    """Serialize one strict model in the repository's canonical JSON form."""

    return (model.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_model(path: str | Path, model: BaseModel) -> None:
    """Atomically write one canonical model artifact."""

    write_bytes(path, model_bytes(model))


def write_bytes(path: str | Path, content: bytes) -> None:
    """Atomically write bytes without silently replacing a partial file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
