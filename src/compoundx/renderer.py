"""Render one concise, evidence-expandable dossier for human review."""

from __future__ import annotations

from compoundx.schemas import (
    ContentValidation,
    DossierSection,
    ExternalSource,
    ReviewReport,
    SectionFinding,
    TraceableContent,
    TraceablePoint,
    TranslationSection,
)


SECTION_LABELS = {
    DossierSection.PRODUCT_INTENT: "Product intent",
    DossierSection.COMPONENT_FUNCTION: "Component function",
    DossierSection.PERFORMANCE_TO_VALIDATE: "Performance to validate",
    DossierSection.MATERIAL_RELEVANT_CRITERIA: "Material-relevant criteria",
    DossierSection.MISSING_INFORMATION: "Missing information",
    DossierSection.CONFLICTS_AND_TRADEOFFS: "Conflicts and trade-offs",
}


def render_markdown(report: ReviewReport) -> str:
    validation_by_owner = {
        validation.owner: validation
        for validation in report.translation_validation.contents
    }
    findings_by_section = {
        finding.section: finding
        for finding in (report.critic.findings if report.critic else ())
    }
    external_by_id = {
        source.source_id: source for source in report.external_sources
    }
    lines = [
        "# Traceable technical translation",
        "",
        f"Source: `{report.source.name}`",
        f"Source format: `{report.source.source_format.value}`",
        f"Source SHA-256: `{report.source.sha256}`",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "## Review summary",
        "",
        "- Dossier sections: 6",
        "- Translation levels: 4",
        f"- Missing-information points: {len(report.translation.missing_information)}",
        f"- Conflicts and trade-offs: {len(report.translation.conflicts_and_tradeoffs)}",
        "- Mechanical validation: "
        f"{report.translation_validation.valid_content_count} valid, "
        f"{report.translation_validation.invalid_content_count} invalid",
        "- Bounded translator repair: "
        + (
            "completed and fully recorded"
            if report.translator_repair_call is not None
            else (
                f"failed ({report.translator_repair_error})"
                if report.translator_repair_error is not None
                else "not needed"
            )
        ),
        "- Critic sections requiring human review: "
        f"{sum(f.human_review_required for f in findings_by_section.values())}",
        "- Critic mechanical validation: "
        + (
            f"{len(report.critic_validation.errors)} errors"
            if report.critic_validation is not None
            else "not completed"
        ),
        "",
        "> Mechanical validation proves exact citations and numeric provenance only. "
        "It does not prove that an engineering interpretation is correct or complete.",
        "",
    ]

    primary = (
        (DossierSection.PRODUCT_INTENT, report.translation.product_intent),
        (DossierSection.COMPONENT_FUNCTION, report.translation.component_function),
        (
            DossierSection.PERFORMANCE_TO_VALIDATE,
            report.translation.performance_to_validate,
        ),
        (
            DossierSection.MATERIAL_RELEVANT_CRITERIA,
            report.translation.material_relevant_criteria,
        ),
    )
    for index, (section_name, section) in enumerate(primary, start=1):
        lines.extend(
            _render_section(
                index=index,
                section_name=section_name,
                section=section,
                validation=validation_by_owner[section_name.value],
                finding=findings_by_section.get(section_name),
                external_by_id=external_by_id,
            )
        )

    lines.extend(
        _render_points(
            heading="5. Missing information",
            owner_prefix="missing_information",
            points=report.translation.missing_information,
            validation_by_owner=validation_by_owner,
            finding=findings_by_section.get(DossierSection.MISSING_INFORMATION),
            external_by_id=external_by_id,
        )
    )
    lines.extend(
        _render_points(
            heading="6. Conflicts and trade-offs",
            owner_prefix="conflicts_and_tradeoffs",
            points=report.translation.conflicts_and_tradeoffs,
            validation_by_owner=validation_by_owner,
            finding=findings_by_section.get(
                DossierSection.CONFLICTS_AND_TRADEOFFS
            ),
            external_by_id=external_by_id,
        )
    )

    if report.critic is not None:
        lines.extend(["## Critic additions", ""])
        lines.extend(
            _simple_points(
                "Additional missing information",
                report.critic.additional_missing_information,
            )
        )
        lines.extend(
            _simple_points(
                "Additional trade-offs",
                report.critic.additional_tradeoffs,
            )
        )
        if report.critic.additional_open_questions:
            lines.extend(["### Additional open questions", ""])
            lines.extend(
                f"- {question}"
                for question in report.critic.additional_open_questions
            )
            lines.append("")
        if report.critic_validation is not None and report.critic_validation.errors:
            lines.extend(["### Critic validation errors", ""])
            lines.extend(
                f"- `{error.field or 'critic'}` / `{error.code.value}`: "
                f"{error.message}"
                + (f" (`{error.value}`)" if error.value else "")
                for error in report.critic_validation.errors
            )
            lines.append("")
    elif report.critic_error is not None:
        lines.extend(
            [
                "## Critic status",
                "",
                "The critic pass failed. The mechanically checked dossier remains "
                "available, but semantic review is incomplete.",
                "",
                f"`{report.critic_error}`",
                "",
            ]
        )

    if report.external_sources:
        lines.extend(["## External source snapshots", ""])
        for source in report.external_sources:
            lines.extend(
                [
                    f"- **{source.title}** — {source.locator}",
                    f"  - Source ID: `{source.source_id}`",
                    f"  - Snapshot SHA-256: `{source.content_sha256}`",
                ]
            )
        lines.append("")

    lines.extend(["## Known limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report.known_limitations)
    lines.extend(
        [
            "",
            "## Human decision",
            "",
            "- [ ] Review the four-level reasoning chain.",
            "- [ ] Resolve invalid citations or unsupported numbers.",
            "- [ ] Confirm missing information and trade-offs.",
            "- [ ] Record corrections before any material decision.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_section(
    *,
    index: int,
    section_name: DossierSection,
    section: TranslationSection,
    validation: ContentValidation,
    finding: SectionFinding | None,
    external_by_id: dict[str, ExternalSource],
) -> list[str]:
    lines = [
        f"## {index}. {SECTION_LABELS[section_name]}",
        "",
        section.text,
        "",
        f"**Mechanical status:** {'valid' if validation.valid else 'invalid'}",
        "",
    ]
    if section.assumptions:
        lines.extend(["**Assumptions and interpretations**", ""])
        lines.extend(f"- {assumption}" for assumption in section.assumptions)
        lines.append("")
    if section.uncertainties:
        lines.extend(["**Uncertainties**", ""])
        lines.extend(f"- {uncertainty}" for uncertainty in section.uncertainties)
        lines.append("")
    lines.extend(_critic_finding(finding))
    lines.extend(
        _citations(
            section,
            validation,
            external_by_id,
        )
    )
    lines.extend(_validation_errors(validation))
    return lines


def _render_points(
    *,
    heading: str,
    owner_prefix: str,
    points: tuple[TraceablePoint, ...],
    validation_by_owner: dict[str, ContentValidation],
    finding: SectionFinding | None,
    external_by_id: dict[str, ExternalSource],
) -> list[str]:
    lines = [f"## {heading}", ""]
    lines.extend(_critic_finding(finding))
    for index, point in enumerate(points, start=1):
        owner = f"{owner_prefix}[{index}]"
        validation = validation_by_owner[owner]
        status = "valid" if validation.valid else "invalid"
        lines.extend([f"{index}. {point.text}", f"   - Mechanical status: **{status}**"])
        for quote in point.evidence_quotes:
            span = next(
                (span for span in validation.evidence_spans if span.quote == quote),
                None,
            )
            location = _span_location(span, prefix="")
            displayed_quote = (
                span.source_quote if span is not None and span.source_quote else quote
            )
            lines.append(
                f"   - Evidence ({location}): “{_one_line(displayed_quote)}”"
            )
        for citation in point.external_citations:
            source = external_by_id.get(citation.source_id)
            label = source.title if source is not None else citation.source_id
            lines.append(f"   - External context ({label}): “{_one_line(citation.quote)}”")
        for error in validation.errors:
            lines.append(f"   - Validation error `{error.code.value}`: {error.message}")
        lines.append("")
    return lines


def _critic_finding(finding: SectionFinding | None) -> list[str]:
    if finding is None:
        return []
    lines = [
        f"**Critic:** {finding.assessment.value}. {finding.explanation}",
        "**Semantic support:** "
        + (
            finding.support_assessment.value
            if finding.support_assessment is not None
            else "not recorded by this report version"
        ),
        f"**Human review required:** {'yes' if finding.human_review_required else 'no'}",
        "",
    ]
    if finding.issue_types:
        lines.extend(
            [
                "**Issue types:** "
                + ", ".join(issue.value for issue in finding.issue_types),
                "",
            ]
        )
    if finding.translated_excerpts:
        lines.extend(["**Translated wording under review**", ""])
        lines.extend(f"> {_one_line(excerpt)}" for excerpt in finding.translated_excerpts)
        lines.append("")
    if finding.suggested_revision:
        lines.extend([f"**Suggested revision:** {finding.suggested_revision}", ""])
    return lines


def _citations(
    content: TraceableContent,
    validation: ContentValidation,
    external_by_id: dict[str, ExternalSource],
) -> list[str]:
    lines = ["**Evidence**", ""]
    for quote in content.evidence_quotes:
        span = next(
            (span for span in validation.evidence_spans if span.quote == quote),
            None,
        )
        location = _span_location(span)
        displayed_quote = (
            span.source_quote if span is not None and span.source_quote else quote
        )
        lines.append(f"> {_one_line(displayed_quote)} ({location})")
    for citation in content.external_citations:
        source = external_by_id.get(citation.source_id)
        label = source.title if source is not None else citation.source_id
        lines.append(f"> {_one_line(citation.quote)} (external: {label})")
    lines.append("")
    return lines


def _validation_errors(validation: ContentValidation) -> list[str]:
    if not validation.errors:
        return []
    lines = ["**Mechanical validation errors**", ""]
    lines.extend(
        f"- `{error.code.value}`: {error.message}"
        for error in validation.errors
    )
    lines.append("")
    return lines


def _simple_points(title: str, points: tuple[TraceablePoint, ...]) -> list[str]:
    if not points:
        return [f"### {title}", "", "None proposed.", ""]
    return [f"### {title}", "", *(f"- {point.text}" for point in points), ""]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _span_location(span, *, prefix: str = "source ") -> str:
    if span is None:
        return "not mechanically verified"
    page = f"page {span.page_number}, " if span.page_number is not None else ""
    return f"{page}{prefix}characters {span.start}–{span.end}"
