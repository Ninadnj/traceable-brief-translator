"""Deterministic citation and number checks for the six-section dossier."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from compoundx.models import NumericTokenKind, SourceDocument
from compoundx.quantities import extract_numeric_tokens
from compoundx.citation_matching import normalized_exact_matches
from compoundx.schemas import (
    ContentValidation,
    CriticEvidenceSpan,
    CriticExternalEvidenceSpan,
    CriticIssueType,
    CriticOutput,
    CriticValidation,
    DossierSection,
    DossierValidation,
    EvidenceSpan,
    ExternalEvidenceSpan,
    ExternalSource,
    PRIMARY_SECTIONS,
    SourcePageSpan,
    SectionFinding,
    TechnicalDossier,
    TraceableContent,
    TraceablePoint,
    TranslationSection,
    ValidationError,
    ValidationErrorCode,
)


def dossier_contents(
    dossier: TechnicalDossier,
) -> tuple[tuple[str, TraceableContent, tuple[str, ...]], ...]:
    """Return the fixed sections and bounded list entries in display order."""

    contents: list[tuple[str, TraceableContent, tuple[str, ...]]] = []
    for section_name in PRIMARY_SECTIONS:
        section = getattr(dossier, section_name.value)
        contents.append(
            (
                section_name.value,
                section,
                (section.text, *section.assumptions, *section.uncertainties),
            )
        )
    for index, point in enumerate(dossier.missing_information, start=1):
        contents.append(
            (f"missing_information[{index}]", point, (point.text,))
        )
    for index, point in enumerate(dossier.conflicts_and_tradeoffs, start=1):
        contents.append(
            (f"conflicts_and_tradeoffs[{index}]", point, (point.text,))
        )
    return tuple(contents)


def verify_dossier(
    source: SourceDocument,
    dossier: TechnicalDossier,
    external_sources: tuple[ExternalSource, ...] = (),
    page_spans: tuple[SourcePageSpan, ...] = (),
) -> DossierValidation:
    """Verify only claims code can prove: citations and numeric provenance."""

    return DossierValidation(
        contents=tuple(
            _validate_content(
                source=source,
                owner=owner,
                content=content,
                output_texts=output_texts,
                external_sources=external_sources,
                page_spans=page_spans,
            )
            for owner, content, output_texts in dossier_contents(dossier)
        )
    )


def verify_critic(
    source: SourceDocument,
    critic: CriticOutput,
    external_sources: tuple[ExternalSource, ...] = (),
    page_spans: tuple[SourcePageSpan, ...] = (),
    dossier: TechnicalDossier | None = None,
    dossier_validation: DossierValidation | None = None,
    *,
    require_current_contract: bool = False,
) -> CriticValidation:
    """Verify critic coverage and exact citations without judging semantics."""

    errors: list[ValidationError] = []
    evidence_spans: list[CriticEvidenceSpan] = []
    external_spans: list[CriticExternalEvidenceSpan] = []
    section_counts = Counter(finding.section for finding in critic.findings)

    current_contract = critic.contract_version == "compoundx.critic.v2"
    if require_current_contract and not current_contract:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "critic did not return the current structured review contract",
                field="contract_version",
                value=str(critic.contract_version),
            )
        )

    for section in DossierSection:
        count = section_counts[section]
        if count == 0:
            errors.append(
                _error(
                    ValidationErrorCode.MISSING_SECTION_FINDING,
                    f"critic did not review required section {section.value}",
                    field="findings",
                    value=section.value,
                )
            )
        elif count > 1:
            errors.append(
                _error(
                    ValidationErrorCode.DUPLICATE_SECTION_FINDING,
                    f"critic reviewed section {section.value} more than once",
                    field="findings",
                    value=section.value,
                )
            )

    for finding in critic.findings:
        owner = f"finding.{finding.section.value}"
        if current_contract:
            errors.extend(
                _validate_finding_contract(
                    finding=finding,
                    owner=owner,
                    dossier=dossier,
                    dossier_validation=dossier_validation,
                )
            )
        for quote in finding.evidence_quotes:
            span, error = _resolve_brief_quote(
                source.text,
                quote,
                page_spans=page_spans,
            )
            if error is not None:
                errors.append(error.model_copy(update={"field": owner}))
            elif span is not None:
                evidence_spans.append(
                    CriticEvidenceSpan(owner=owner, **span.model_dump())
                )

    additions: tuple[tuple[str, TraceablePoint], ...] = tuple(
        (
            f"additional_missing_information[{index}]",
            point,
        )
        for index, point in enumerate(
            critic.additional_missing_information,
            start=1,
        )
    ) + tuple(
        (f"additional_tradeoffs[{index}]", point)
        for index, point in enumerate(critic.additional_tradeoffs, start=1)
    )
    for owner, point in additions:
        validation = _validate_content(
            source=source,
            owner=owner,
            content=point,
            output_texts=(point.text,),
            external_sources=external_sources,
            page_spans=page_spans,
        )
        errors.extend(validation.errors)
        evidence_spans.extend(
            CriticEvidenceSpan(owner=owner, **span.model_dump())
            for span in validation.evidence_spans
        )
        external_spans.extend(
            CriticExternalEvidenceSpan(owner=owner, **span.model_dump())
            for span in validation.external_evidence_spans
        )

    return CriticValidation(
        evidence_spans=tuple(evidence_spans),
        external_evidence_spans=tuple(external_spans),
        errors=tuple(errors),
    )


def _validate_finding_contract(
    *,
    finding: SectionFinding,
    owner: str,
    dossier: TechnicalDossier | None,
    dossier_validation: DossierValidation | None,
) -> list[ValidationError]:
    """Validate the critic's structure, never the truth of its semantic opinion."""

    errors: list[ValidationError] = []
    adverse = finding.assessment.value != "supported"
    if finding.support_assessment is None:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "critic finding is missing a support assessment",
                field=owner,
            )
        )
    if finding.human_review_required != adverse:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "human_review_required must be true exactly for revise or uncertain findings",
                field=owner,
            )
        )
    if adverse and not finding.issue_types:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "revise and uncertain findings require at least one issue type",
                field=owner,
            )
        )
    excerpt_issue_types = set(finding.issue_types) - {
        CriticIssueType.OMISSION,
        CriticIssueType.MECHANICAL_VALIDATION,
    }
    if adverse and excerpt_issue_types and not finding.translated_excerpts:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "this issue type requires an exact translated excerpt",
                field=owner,
            )
        )
    if not adverse and finding.issue_types:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "supported findings cannot carry unresolved issue types",
                field=owner,
            )
        )
    if finding.assessment.value == "revise" and not finding.suggested_revision:
        errors.append(
            _error(
                ValidationErrorCode.INVALID_CRITIC_CONTRACT,
                "revise findings require a concrete suggested revision",
                field=owner,
            )
        )

    if dossier is not None:
        section_texts = _section_texts(dossier, finding.section)
        for excerpt in finding.translated_excerpts:
            matches = sum(
                bool(normalized_exact_matches(text, excerpt))
                for text in section_texts
            )
            if not matches:
                errors.append(
                    _error(
                        ValidationErrorCode.MISSING_TRANSLATED_EXCERPT,
                        "critic excerpt does not appear in the translated section",
                        field=owner,
                        value=excerpt,
                    )
                )

    invalid_section = False
    if dossier_validation is not None:
        prefix = finding.section.value
        invalid_section = any(
            not content.valid
            and (
                content.owner == prefix
                or content.owner.startswith(f"{prefix}[")
            )
            for content in dossier_validation.contents
        )
    if invalid_section and (
        finding.assessment.value != "revise"
        or not finding.human_review_required
        or CriticIssueType.MECHANICAL_VALIDATION not in finding.issue_types
    ):
        errors.append(
            _error(
                ValidationErrorCode.UNRESOLVED_MECHANICAL_ERROR,
                "critic must mark a mechanically invalid section for revision",
                field=owner,
                value=finding.section.value,
            )
        )
    return errors


def _section_texts(
    dossier: TechnicalDossier,
    section: DossierSection,
) -> tuple[str, ...]:
    value = getattr(dossier, section.value)
    if isinstance(value, TranslationSection):
        return (value.text, *value.assumptions, *value.uncertainties)
    return tuple(point.text for point in value)


def _validate_content(
    *,
    source: SourceDocument,
    owner: str,
    content: TraceableContent,
    output_texts: tuple[str, ...],
    external_sources: tuple[ExternalSource, ...],
    page_spans: tuple[SourcePageSpan, ...],
) -> ContentValidation:
    errors: list[ValidationError] = []
    evidence_spans: list[EvidenceSpan] = []
    external_spans: list[ExternalEvidenceSpan] = []
    verified_texts: list[str] = []
    external_by_id = {external.source_id: external for external in external_sources}

    for quote in content.evidence_quotes:
        span, error = _resolve_brief_quote(
            source.text,
            quote,
            page_spans=page_spans,
        )
        if error is not None:
            errors.append(error.model_copy(update={"field": owner}))
        elif span is not None:
            evidence_spans.append(span)
            verified_texts.append(span.source_quote or quote)

    for citation in content.external_citations:
        external = external_by_id.get(citation.source_id)
        if external is None:
            errors.append(
                _error(
                    ValidationErrorCode.UNKNOWN_EXTERNAL_SOURCE,
                    f"unknown external source {citation.source_id}",
                    field=owner,
                    value=citation.source_id,
                )
            )
            continue
        occurrences = normalized_exact_matches(external.text, citation.quote)
        if not occurrences:
            errors.append(
                _error(
                    ValidationErrorCode.MISSING_EXTERNAL_QUOTE,
                    "external citation does not appear in its frozen source",
                    field=owner,
                    value=citation.quote,
                )
            )
        elif len(occurrences) > 1:
            errors.append(
                _error(
                    ValidationErrorCode.AMBIGUOUS_EXTERNAL_QUOTE,
                    "external citation appears more than once in its frozen source",
                    field=owner,
                    value=citation.quote,
                )
            )
        else:
            match = occurrences[0]
            external_spans.append(
                ExternalEvidenceSpan(
                    source_id=citation.source_id,
                    quote=citation.quote,
                    source_quote=match.source_quote,
                    start=match.start,
                    end=match.end,
                )
            )
            verified_texts.append(match.source_quote)

    supported_values = {
        token.value
        for token in extract_numeric_tokens("\n".join(verified_texts))
        if token.kind is not NumericTokenKind.IDENTIFIER
    }
    unsupported_numbers: list[str] = []
    for token in _numeric_tokens(output_texts):
        if token.value not in supported_values:
            unsupported_numbers.append(token.raw_text)
            errors.append(
                _error(
                    ValidationErrorCode.UNSUPPORTED_NUMBER,
                    "output number does not appear in verified evidence",
                    field=owner,
                    value=token.raw_text,
                )
            )

    return ContentValidation(
        owner=owner,
        valid=not errors,
        evidence_spans=tuple(evidence_spans),
        external_evidence_spans=tuple(external_spans),
        errors=tuple(errors),
        unsupported_numbers=tuple(dict.fromkeys(unsupported_numbers)),
    )


def _numeric_tokens(texts: Iterable[str]):
    for text in texts:
        for token in extract_numeric_tokens(text):
            if token.kind is not NumericTokenKind.IDENTIFIER:
                yield token


def _resolve_brief_quote(
    source_text: str,
    quote: str,
    *,
    page_spans: tuple[SourcePageSpan, ...] = (),
) -> tuple[EvidenceSpan | None, ValidationError | None]:
    occurrences = normalized_exact_matches(source_text, quote)
    if not occurrences:
        return None, _error(
            ValidationErrorCode.MISSING_QUOTE,
            "citation does not appear in the product brief",
            value=quote,
        )
    if len(occurrences) > 1:
        return None, _error(
            ValidationErrorCode.AMBIGUOUS_QUOTE,
            "citation appears more than once in the product brief",
            value=quote,
        )
    match = occurrences[0]
    return (
        EvidenceSpan(
            quote=quote,
            source_quote=match.source_quote,
            start=match.start,
            end=match.end,
            page_number=_page_number(match.start, match.end, page_spans),
        ),
        None,
    )


def _page_number(
    start: int,
    end: int,
    page_spans: tuple[SourcePageSpan, ...],
) -> int | None:
    for page in page_spans:
        if page.start <= start and end <= page.end:
            return page.page_number
    return None


def _error(
    code: ValidationErrorCode,
    message: str,
    *,
    field: str | None = None,
    value: str | None = None,
) -> ValidationError:
    return ValidationError(
        code=code,
        message=message,
        field=field,
        value=value,
    )
