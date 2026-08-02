"""One end-to-end workflow: translate, verify, critique, and report."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from compoundx.artifact_io import utc_now, write_bytes, write_model
from compoundx.critic import critique
from compoundx.external_sources import load_external_sources
from compoundx.model_client import ModelCallError, StructuredModelClient
from compoundx.renderer import render_markdown
from compoundx.schemas import (
    ReviewReport,
    SourceSummary,
)
from compoundx.source_loader import load_source_document
from compoundx.translator import translate
from compoundx.translation_repair import repair_translation
from compoundx.verifier import verify_critic, verify_dossier


LOGGER = logging.getLogger(__name__)


KNOWN_LIMITATIONS = (
    "The dossier separates synthesis, evidence, assumptions and uncertainty, but "
    "semantic support and completeness remain model and human judgements.",
    "Section-level critic findings are traceable proposals, not deterministic proof "
    "of entailment; a distinct --critic-model reduces correlated errors but does "
    "not eliminate them.",
    "Invalid and unsupported content remains visible and explicitly flagged.",
    "When the initial dossier fails deterministic checks, one bounded model repair "
    "is attempted and both the initial and final states are retained; a remaining "
    "error still blocks acceptance.",
    "Run directories are immutable, but multi-reviewer decision history is outside "
    "this prototype.",
    "The core dossier does not calculate derived targets; engineering calculations "
    "remain a separate human-review task.",
    "Text-based PDFs are supported; scanned PDFs require OCR, and citation offsets "
    "refer to extracted text rather than visual page coordinates.",
    "External source snapshots are caller supplied and exact-quote verified; the "
    "system does not establish that a source is authoritative or applicable.",
    "Human review remains required for engineering judgement and final decisions.",
)


@dataclass(frozen=True)
class RunResult:
    report: ReviewReport
    json_path: Path
    markdown_path: Path

    @property
    def has_mechanical_errors(self) -> bool:
        return (
            self.report.translation_validation.invalid_content_count > 0
            or (
                self.report.critic_validation is not None
                and bool(self.report.critic_validation.errors)
            )
            or self.report.critic_error is not None
        )


def run_translation(
    *,
    source_path: str | Path,
    output_dir: str | Path,
    client: StructuredModelClient,
    critic_client: StructuredModelClient | None = None,
    external_sources_path: str | Path | None = None,
) -> RunResult:
    output = Path(output_dir)
    if output.exists():
        raise OutputExistsError(
            f"output directory already exists and will not be overwritten: "
            f"{output}"
        )

    loaded_source = load_source_document(source_path)
    source = loaded_source.document
    external_sources = load_external_sources(external_sources_path)
    LOGGER.info("Running translator pass for %s", source.source_name)
    translator_result = translate(client, source, external_sources)
    translation_validation = verify_dossier(
        source,
        translator_result.output,
        external_sources,
        loaded_source.page_spans,
    )
    LOGGER.info(
        "Translator produced one dossier: %d traceable contents valid, %d invalid",
        translation_validation.valid_content_count,
        translation_validation.invalid_content_count,
    )

    initial_translation = None
    initial_translation_validation = None
    initial_translator_call = None
    repair_result = None
    repair_error = None
    if translation_validation.invalid_content_count:
        try:
            LOGGER.info(
                "Running one bounded translator repair pass for %d invalid contents",
                translation_validation.invalid_content_count,
            )
            repair_result = repair_translation(
                client,
                source,
                translator_result.output,
                translation_validation,
                external_sources,
            )
            initial_translation = translator_result.output
            initial_translation_validation = translation_validation
            initial_translator_call = translator_result.info
            translator_result = repair_result
            translation_validation = verify_dossier(
                source,
                translator_result.output,
                external_sources,
                loaded_source.page_spans,
            )
            LOGGER.info(
                "Repair produced %d traceable contents valid, %d invalid",
                translation_validation.valid_content_count,
                translation_validation.invalid_content_count,
            )
        except ModelCallError as error:
            repair_error = str(error)
            LOGGER.warning(
                "Translator repair failed; preserving the initial invalid output: %s",
                error,
            )

    critic_result = None
    critic_validation = None
    critic_error = None
    try:
        LOGGER.info("Running critic and conflict-detection pass")
        critic_result = critique(
            critic_client or client,
            source,
            translator_result.output,
            translation_validation,
            external_sources,
        )
        critic_validation = verify_critic(
            source,
            critic_result.output,
            external_sources,
            loaded_source.page_spans,
            translator_result.output,
            translation_validation,
            require_current_contract=True,
        )
    except ModelCallError as error:
        critic_error = str(error)
        LOGGER.warning(
            "Critic pass failed; preserving the verified translator output: %s",
            error,
        )

    report = ReviewReport(
        generated_at=utc_now(),
        source=SourceSummary(
            name=source.source_name,
            sha256=source.sha256,
            character_count=len(source.text),
            source_format=loaded_source.source_format,
            page_count=loaded_source.page_count,
        ),
        source_text=source.text,
        source_pages=loaded_source.page_spans,
        external_sources=external_sources,
        translator_call=(
            repair_result.info if repair_result is not None else translator_result.info
        ),
        initial_translator_call=initial_translator_call,
        initial_translation=initial_translation,
        initial_translation_validation=initial_translation_validation,
        translator_repair_call=repair_result.info if repair_result is not None else None,
        translator_repair_error=repair_error,
        translation=translator_result.output,
        translation_validation=translation_validation,
        critic_call=critic_result.info if critic_result is not None else None,
        critic=critic_result.output if critic_result is not None else None,
        critic_validation=critic_validation,
        critic_error=critic_error,
        known_limitations=KNOWN_LIMITATIONS,
    )

    json_path = output / "report.json"
    markdown_path = output / "report.md"
    write_model(json_path, report)
    write_bytes(markdown_path, render_markdown(report).encode("utf-8"))
    LOGGER.info("Wrote review report to %s", markdown_path)
    return RunResult(
        report=report,
        json_path=json_path,
        markdown_path=markdown_path,
    )


class OutputExistsError(FileExistsError):
    """Raised instead of replacing a prior run."""
