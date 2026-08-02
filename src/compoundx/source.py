"""Exact UTF-8 source loading: byte-faithful text plus its content digest."""

from __future__ import annotations

import hashlib
from pathlib import Path

from compoundx.models import SourceDocument


def load_source(path: str | Path) -> SourceDocument:
    """Load one explicitly supplied UTF-8 document without discovery."""

    source_path = Path(path)
    source_bytes = source_path.read_bytes()
    text = source_bytes.decode("utf-8", errors="strict")
    return SourceDocument(
        source_name=source_path.name,
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        text=text,
    )
