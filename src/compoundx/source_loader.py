"""One input boundary for UTF-8 documents and text-based PDFs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from compoundx.models import SourceDocument
from compoundx.source import load_source
from compoundx.schemas import SourceFormat, SourcePageSpan


@dataclass(frozen=True)
class LoadedSource:
    document: SourceDocument
    source_format: SourceFormat
    page_count: int | None = None
    page_spans: tuple[SourcePageSpan, ...] = ()


def load_source_document(path: str | Path) -> LoadedSource:
    source_path = Path(path)
    if source_path.suffix.casefold() != ".pdf":
        return LoadedSource(
            document=load_source(source_path),
            source_format=SourceFormat.TEXT,
        )

    source_bytes = source_path.read_bytes()
    try:
        reader = PdfReader(source_path)
    except PdfReadError as error:
        raise SourceError(f"could not read PDF: {error}") from error
    if reader.is_encrypted:
        raise SourceError("encrypted PDFs are not supported")

    page_texts = [
        (page.extract_text() or "").rstrip()
        for page in reader.pages
    ]
    if not any(text.strip() for text in page_texts):
        raise SourceError(
            "PDF contains no extractable text; run OCR before using it"
        )
    extracted_text = "\n\n".join(page_texts)
    page_spans: list[SourcePageSpan] = []
    cursor = 0
    for page_number, page_text in enumerate(page_texts, start=1):
        page_spans.append(
            SourcePageSpan(
                page_number=page_number,
                start=cursor,
                end=cursor + len(page_text),
            )
        )
        cursor += len(page_text)
        if page_number < len(page_texts):
            cursor += 2
    if not extracted_text.endswith("\n"):
        extracted_text += "\n"
    return LoadedSource(
        document=SourceDocument(
            source_name=source_path.name,
            sha256=hashlib.sha256(source_bytes).hexdigest(),
            text=extracted_text,
        ),
        source_format=SourceFormat.PDF,
        page_count=len(reader.pages),
        page_spans=tuple(page_spans),
    )


class SourceError(ValueError):
    """Raised when the source cannot become a traceable text representation."""
