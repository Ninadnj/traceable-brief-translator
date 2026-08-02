"""Load an explicit, immutable external-source pack without web retrieval."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import Field, model_validator

from compoundx.models import CompoundXModel
from compoundx.schemas import ExternalSource


class ExternalSourceInput(CompoundXModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    retrieved_at: datetime | None = None
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def text_fields_are_not_blank(self) -> ExternalSourceInput:
        if not all(
            value.strip()
            for value in (self.source_id, self.title, self.locator, self.text)
        ):
            raise ValueError("external source fields cannot be blank")
        return self


class ExternalSourcePackInput(CompoundXModel):
    sources: tuple[ExternalSourceInput, ...] = Field(min_length=1)


def load_external_sources(
    path: str | Path | None,
) -> tuple[ExternalSource, ...]:
    """Load supplied source snapshots and assign content digests in code."""

    if path is None:
        return ()
    source_path = Path(path)
    pack = ExternalSourcePackInput.model_validate_json(
        source_path.read_text(encoding="utf-8")
    )
    id_counts = Counter(source.source_id for source in pack.sources)
    duplicate_ids = sorted(
        source_id
        for source_id, count in id_counts.items()
        if count > 1
    )
    if duplicate_ids:
        raise ExternalSourcePackError(
            "external source IDs must be unique: "
            + ", ".join(duplicate_ids)
        )
    return tuple(
        ExternalSource(
            source_id=source.source_id,
            title=source.title,
            locator=source.locator,
            retrieved_at=source.retrieved_at,
            content_sha256=hashlib.sha256(
                source.text.encode("utf-8")
            ).hexdigest(),
            text=source.text,
        )
        for source in pack.sources
    )


class ExternalSourcePackError(ValueError):
    """Raised when a supplied source pack has ambiguous provenance."""
