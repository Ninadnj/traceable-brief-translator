"""Deterministic PDF renderer for finalized section-level reviews."""

from __future__ import annotations

import os
import re
from datetime import UTC
from pathlib import Path
from xml.sax.saxutils import escape

from compoundx.schemas import (
    ContentValidation,
    DossierSection,
    ExternalCitation,
    ReviewReport,
    TraceableContent,
)
from compoundx.section_review import (
    ALL_SECTIONS,
    NARRATIVE_SECTIONS,
    ListItemReviewStatus,
    ListSectionReview,
    NarrativeSectionReview,
    SectionReviewArtifact,
    SectionReviewStatus,
)


def write_review_pdf(
    path: str | Path,
    *,
    artifact: SectionReviewArtifact,
    report: ReviewReport,
    reviewed_artifact_sha256: str,
) -> None:
    """Write a concise combined report without using a UI screenshot."""

    from reportlab import __file__ as reportlab_file
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    target = Path(path)
    if target.exists():
        raise FileExistsError(f"review PDF already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")

    fonts_dir = Path(reportlab_file).resolve().parent / "fonts"
    if "CXBody" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CXBody", fonts_dir / "Vera.ttf"))
        pdfmetrics.registerFont(TTFont("CXBold", fonts_dir / "VeraBd.ttf"))
        pdfmetrics.registerFont(TTFont("CXItalic", fonts_dir / "VeraIt.ttf"))

    palette = {
        "ink": colors.HexColor("#182230"),
        "muted": colors.HexColor("#526071"),
        "blue": colors.HexColor("#2457A7"),
        "light_blue": colors.HexColor("#EAF1FB"),
        "green": colors.HexColor("#176B46"),
        "human": colors.HexColor("#EAF7F0"),
        "light_amber": colors.HexColor("#FFF5D8"),
        "evidence": colors.HexColor("#F4F6F8"),
        "line": colors.HexColor("#CFD7E2"),
        "white": colors.white,
    }
    sample = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "CXTitle",
            parent=sample["Title"],
            fontName="CXBold",
            fontSize=23,
            leading=28,
            alignment=TA_CENTER,
            textColor=palette["ink"],
            spaceAfter=10,
        ),
        "subtitle": ParagraphStyle(
            "CXSubtitle",
            parent=sample["Normal"],
            fontName="CXBody",
            fontSize=10.5,
            leading=14,
            alignment=TA_CENTER,
            textColor=palette["muted"],
            spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "CXH1",
            parent=sample["Heading1"],
            fontName="CXBold",
            fontSize=15,
            leading=19,
            textColor=palette["ink"],
            spaceBefore=8,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h2_model": ParagraphStyle(
            "CXH2Model",
            parent=sample["Heading2"],
            fontName="CXBold",
            fontSize=10.5,
            leading=13,
            textColor=palette["blue"],
            backColor=palette["light_blue"],
            borderPadding=5,
            spaceBefore=4,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "h2_human": ParagraphStyle(
            "CXH2Human",
            parent=sample["Heading2"],
            fontName="CXBold",
            fontSize=10.5,
            leading=13,
            textColor=palette["green"],
            backColor=palette["human"],
            borderPadding=5,
            spaceBefore=6,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "CXBodyStyle",
            parent=sample["BodyText"],
            fontName="CXBody",
            fontSize=9,
            leading=12.5,
            textColor=palette["ink"],
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "CXSmall",
            parent=sample["BodyText"],
            fontName="CXBody",
            fontSize=7.6,
            leading=10.2,
            textColor=palette["muted"],
            spaceAfter=3,
        ),
        "label": ParagraphStyle(
            "CXLabel",
            parent=sample["BodyText"],
            fontName="CXBold",
            fontSize=8,
            leading=10.5,
            textColor=palette["ink"],
            spaceBefore=3,
            spaceAfter=2,
            keepWithNext=True,
        ),
        "status": ParagraphStyle(
            "CXStatus",
            parent=sample["BodyText"],
            fontName="CXBold",
            fontSize=8.3,
            leading=11,
            textColor=palette["green"],
            spaceAfter=3,
        ),
        "evidence": ParagraphStyle(
            "CXEvidence",
            parent=sample["BodyText"],
            fontName="CXBody",
            fontSize=7.8,
            leading=10.5,
            leftIndent=6,
            rightIndent=6,
            borderColor=palette["line"],
            borderWidth=0.4,
            borderPadding=5,
            backColor=palette["evidence"],
            spaceAfter=4,
        ),
        "mono": ParagraphStyle(
            "CXMono",
            parent=sample["BodyText"],
            fontName="Courier",
            fontSize=6.8,
            leading=9,
            textColor=palette["muted"],
            spaceAfter=2,
        ),
    }

    doc = SimpleDocTemplate(
        str(temporary),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title="Human-reviewed technical translation",
        author=artifact.reviewer_id,
    )
    story = []

    def paragraph(text: str, style: str = "body"):
        return Paragraph(_safe(text), styles[style])

    def card(flowables, *, background=None, border=None):
        table = Table([[flowables]], colWidths=(168 * mm,), hAlign="CENTER")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOX", (0, 0), (-1, -1), 0.5, border or palette["line"]),
                    ("BACKGROUND", (0, 0), (-1, -1), background or palette["white"]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return KeepTogether([table, Spacer(1, 3 * mm)])

    validation_by_owner = {
        item.owner: item for item in artifact.validation.contents
    }
    model_validation_by_owner = {
        item.owner: item for item in report.translation_validation.contents
    }

    _append_cover(
        story,
        artifact=artifact,
        report=report,
        reviewed_artifact_sha256=reviewed_artifact_sha256,
        paragraph=paragraph,
        palette=palette,
        Table=Table,
        TableStyle=TableStyle,
        Spacer=Spacer,
        mm=mm,
    )
    story.append(PageBreak())

    list_evidence: list[tuple[str, str, str]] = []
    for number, section in enumerate(ALL_SECTIONS, start=1):
        review = artifact.sections.get(section)
        critic = next(
            (
                finding
                for finding in (report.critic.findings if report.critic else ())
                if finding.section is section
            ),
            None,
        )
        story.append(paragraph(f"{number}. {_section_label(section)}", "h1"))
        if section in NARRATIVE_SECTIONS:
            assert isinstance(review, NarrativeSectionReview)
            model_content = getattr(report.translation, section.value)
            model_validation = model_validation_by_owner[section.value]
            story.extend(
                [
                    paragraph("Model-generated translation", "h2_model"),
                    paragraph(model_content.text),
                ]
            )
            _append_named_values(
                story, "Assumptions", model_content.assumptions, paragraph
            )
            _append_named_values(
                story, "Uncertainties", model_content.uncertainties, paragraph
            )
            _append_critic(story, critic, paragraph)
            story.append(paragraph("Shared verified source evidence", "label"))
            _append_evidence(
                story,
                model_content,
                model_validation,
                report,
                paragraph,
            )

            if review.status is SectionReviewStatus.APPROVED_AS_GENERATED:
                story.append(
                    card(
                        [
                            paragraph("Human review", "h2_human"),
                            paragraph("APPROVED AS GENERATED", "status"),
                            paragraph(
                                "No changes made. The model translation and shared "
                                "evidence above are the reviewed result.",
                                "small",
                            ),
                        ],
                        background=palette["human"],
                        border=palette["green"],
                    )
                )
            else:
                story.append(PageBreak())
                human_block = [
                    paragraph("Human review", "h2_human"),
                    paragraph(_human_status(review.status.value).upper(), "status"),
                ]
                changed = review.reviewed_content != review.model_content
                if changed:
                    human_block.extend(
                        [
                            paragraph("Human-reviewed translation", "label"),
                            paragraph(review.reviewed_content.text),
                        ]
                    )
                    _append_named_values(
                        human_block,
                        "Reviewed assumptions",
                        review.reviewed_content.assumptions,
                        paragraph,
                    )
                    _append_named_values(
                        human_block,
                        "Reviewed uncertainties",
                        review.reviewed_content.uncertainties,
                        paragraph,
                    )
                else:
                    human_block.append(paragraph("No provisional content changes."))
                if review.rationale:
                    human_block.extend(
                        [
                            paragraph("Reason for correction", "label"),
                            paragraph(review.rationale),
                        ]
                    )
                if review.reviewer_note:
                    human_block.extend(
                        [
                            paragraph("Reviewer note", "label"),
                            paragraph(review.reviewer_note),
                        ]
                    )
                human_block.append(paragraph("Evidence added or removed", "label"))
                _append_evidence_delta(
                    human_block,
                    model=review.model_content,
                    reviewed=review.reviewed_content,
                    model_validation=model_validation,
                    reviewed_validation=validation_by_owner[section.value],
                    report=report,
                    paragraph=paragraph,
                )
                story.append(
                    card(
                        human_block,
                        background=palette["human"],
                        border=palette["green"],
                    )
                )
                if number < len(ALL_SECTIONS):
                    story.append(PageBreak())
        else:
            assert isinstance(review, ListSectionReview)
            _append_critic(story, critic, paragraph)
            status_text = _human_status(review.status.value).upper()
            story.append(paragraph(f"Human review: {status_text}", "h2_human"))
            if review.reviewer_note:
                story.append(paragraph(f"Reviewer note: {review.reviewer_note}"))
            reviewed_index = 0
            for item_number, item in enumerate(review.items, start=1):
                item_block = [
                    paragraph(
                        f"ITEM {item_number} - {_human_status(item.status.value).upper()}",
                        "label",
                    )
                ]
                model_validation = None
                if item.model_item is not None:
                    assert item.model_index is not None
                    model_owner = f"{section.value}[{item.model_index + 1}]"
                    model_validation = model_validation_by_owner[model_owner]
                reviewed_validation = None
                if item.reviewed_item is not None:
                    reviewed_index += 1
                    reviewed_owner = f"{section.value}[{reviewed_index}]"
                    reviewed_validation = validation_by_owner[reviewed_owner]

                if item.status is ListItemReviewStatus.APPROVED_AS_GENERATED:
                    assert item.model_item is not None
                    item_block.append(paragraph(item.model_item.text))
                    item_block.append(
                        paragraph(
                            _evidence_reference_text(
                                item.model_item, model_validation, report
                            ),
                            "small",
                        )
                    )
                elif item.status is ListItemReviewStatus.CORRECTED:
                    assert item.model_item is not None and item.reviewed_item is not None
                    item_block.extend(
                        [
                            paragraph("Model-generated item", "label"),
                            paragraph(item.model_item.text, "small"),
                            paragraph("Human-reviewed item", "label"),
                            paragraph(item.reviewed_item.text),
                        ]
                    )
                    if item.rationale:
                        item_block.append(paragraph(f"Reason: {item.rationale}", "small"))
                    item_block.append(
                        paragraph(
                            _evidence_reference_text(
                                item.reviewed_item, reviewed_validation, report
                            ),
                            "small",
                        )
                    )
                elif item.status is ListItemReviewStatus.REMOVED:
                    assert item.model_item is not None
                    item_block.extend(
                        [
                            paragraph(item.model_item.text),
                            paragraph(f"Removal reason: {item.rationale}", "small"),
                            paragraph(
                                _evidence_reference_text(
                                    item.model_item, model_validation, report
                                ),
                                "small",
                            ),
                        ]
                    )
                else:
                    assert item.reviewed_item is not None
                    item_block.extend(
                        [
                            paragraph(item.reviewed_item.text),
                            paragraph(f"Addition reason: {item.rationale}", "small"),
                            paragraph(
                                _evidence_reference_text(
                                    item.reviewed_item, reviewed_validation, report
                                ),
                                "small",
                            ),
                        ]
                    )
                background = (
                    palette["human"]
                    if item.status is not ListItemReviewStatus.REMOVED
                    else palette["light_amber"]
                )
                story.append(card(item_block, background=background))
                _collect_list_evidence(
                    list_evidence,
                    section=section,
                    item_number=item_number,
                    item=item,
                    model_validation=model_validation,
                    reviewed_validation=reviewed_validation,
                    report=report,
                )
        if number < len(ALL_SECTIONS):
            story.append(Spacer(1, 3 * mm))

    if list_evidence:
        story.append(PageBreak())
        story.append(paragraph("Evidence appendix - list sections", "h1"))
        story.append(
            paragraph(
                "Full source quotations are printed once here. Item cards use page "
                "references to avoid duplicating the same evidence.",
                "small",
            )
        )
        for label, quote, locator in _deduplicate_evidence(list_evidence):
            story.append(
                card(
                    [
                        paragraph(label, "label"),
                        paragraph(f'"{quote}"', "small"),
                        paragraph(locator, "small"),
                    ],
                    background=palette["evidence"],
                )
            )

    story.append(PageBreak())
    _append_final_page(
        story,
        artifact=artifact,
        reviewed_artifact_sha256=reviewed_artifact_sha256,
        paragraph=paragraph,
    )

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("CXBody", 7.5)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(17 * mm, 9 * mm, "CompoundX human-reviewed dossier")
        canvas.drawRightString(
            A4[0] - 17 * mm,
            9 * mm,
            f"Page {document.page}",
        )
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _append_cover(
    story,
    *,
    artifact,
    report,
    reviewed_artifact_sha256,
    paragraph,
    palette,
    Table,
    TableStyle,
    Spacer,
    mm,
) -> None:
    story.extend(
        [
            Spacer(1, 12 * mm),
            paragraph("Human-reviewed technical translation", "title"),
            paragraph(report.source.name, "subtitle"),
        ]
    )
    metadata = (
        ("Overall status", _human_status(artifact.overall_status.value).upper()),
        ("Reviewer", artifact.reviewer_id),
        ("Reviewed", _format_timestamp(artifact.created_at)),
    )
    table = Table(
        [[paragraph(label, "label"), paragraph(value, "body")] for label, value in metadata],
        colWidths=(45 * mm, 115 * mm),
        hAlign="CENTER",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                ("BACKGROUND", (0, 0), (0, -1), palette["evidence"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([table, Spacer(1, 5 * mm)])

    counts = _section_status_counts(artifact)
    summary = [
        [
            paragraph("Approved as generated", "small"),
            paragraph("Corrected", "small"),
            paragraph("Needs further review", "small"),
            paragraph("Validation errors", "small"),
        ],
        [
            paragraph(str(counts[SectionReviewStatus.APPROVED_AS_GENERATED]), "title"),
            paragraph(str(counts[SectionReviewStatus.CORRECTED]), "title"),
            paragraph(str(counts[SectionReviewStatus.NEEDS_FURTHER_REVIEW]), "title"),
            paragraph(str(artifact.validation.invalid_content_count), "title"),
        ],
    ]
    metrics = Table(summary, colWidths=(40 * mm,) * 4, hAlign="CENTER")
    metrics.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, palette["line"]),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                ("BACKGROUND", (0, 0), (-1, 0), palette["light_blue"]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([metrics, Spacer(1, 4 * mm)])
    if artifact.decision_rationale:
        story.extend(
            [
                paragraph("Reviewer decision", "h1"),
                paragraph(artifact.decision_rationale),
            ]
        )
    story.extend(
        [
            paragraph("Compact artifact identity", "label"),
            paragraph(
                f"Source SHA-256: {_short_hash(artifact.source_sha256)}", "mono"
            ),
            paragraph(
                "Base report JSON SHA-256: "
                f"{_short_hash(artifact.accepted_report_sha256)}",
                "mono",
            ),
            paragraph(
                f"Reviewed JSON SHA-256: {_short_hash(reviewed_artifact_sha256)}",
                "mono",
            ),
            paragraph(
                "Full hashes and the external PDF/Markdown manifest are referenced "
                "on the final page.",
                "small",
            ),
        ]
    )


def _append_final_page(story, *, artifact, reviewed_artifact_sha256, paragraph) -> None:
    story.append(paragraph("Review conclusion", "h1"))
    story.append(
        paragraph(
            f"Final verdict: {_human_status(artifact.overall_status.value).upper()}"
        )
    )
    story.append(paragraph("Change summary", "h1"))
    for section in ALL_SECTIONS:
        review = artifact.sections.get(section)
        assert review is not None
        if isinstance(review, NarrativeSectionReview):
            description = _human_status(review.status.value)
        else:
            counts: dict[str, int] = {}
            for item in review.items:
                counts[item.status.value] = counts.get(item.status.value, 0) + 1
            detail = ", ".join(
                f"{_human_status(name)}: {count}" for name, count in counts.items()
            )
            description = f"{_human_status(review.status.value)} ({detail})"
        story.append(paragraph(f"- {_section_label(section)}: {description}"))

    story.append(paragraph("Deterministic validation", "h1"))
    story.append(
        paragraph(
            f"Valid contents: {artifact.validation.valid_content_count}; "
            f"invalid contents: {artifact.validation.invalid_content_count}."
        )
    )
    for content in artifact.validation.contents:
        for error in content.errors:
            story.append(
                paragraph(
                    f"{content.owner} / {error.code.value}: {error.message}",
                    "small",
                )
            )

    story.append(paragraph("Known limitations", "h1"))
    for limitation in artifact.known_limitations:
        story.append(paragraph(f"- {limitation}", "small"))

    story.append(paragraph("Artifact references and hashes", "h1"))
    story.extend(
        [
            paragraph(f"Source SHA-256: {artifact.source_sha256}", "mono"),
            paragraph(
                f"Base report JSON SHA-256: {artifact.accepted_report_sha256}",
                "mono",
            ),
            paragraph(
                f"Reviewed JSON SHA-256: {reviewed_artifact_sha256}", "mono"
            ),
            paragraph(
                "The sibling manifest.json records the finalized Markdown and PDF "
                "SHA-256 values after those files are written. The PDF does not embed "
                "its own hash because that would change the PDF bytes.",
                "small",
            ),
        ]
    )


def _append_critic(story, critic, paragraph) -> None:
    if critic is None:
        return
    story.extend(
        [
            paragraph("Model critic assessment (before human review)", "label"),
            paragraph(
                f"{_human_status(critic.assessment.value)}: {critic.explanation}",
                "small",
            ),
        ]
    )
    support = (
        _human_status(critic.support_assessment.value)
        if critic.support_assessment is not None
        else "Not recorded by this report version"
    )
    story.append(paragraph(f"Semantic support: {support}", "small"))
    if critic.issue_types:
        story.append(
            paragraph(
                "Issue types: "
                + ", ".join(_human_status(issue.value) for issue in critic.issue_types),
                "small",
            )
        )
    for excerpt in critic.translated_excerpts:
        story.append(paragraph(f'Translated wording: "{excerpt}"', "small"))


def _append_named_values(story, label, values, paragraph) -> None:
    if not values:
        return
    story.append(paragraph(label, "label"))
    for value in values:
        story.append(paragraph(f"- {value}", "small"))


def _append_evidence(story, content, validation, report, paragraph) -> None:
    for quote in content.evidence_quotes:
        span = next(
            (span for span in validation.evidence_spans if span.quote == quote),
            None,
        )
        page = (
            f"Page {span.page_number}"
            if span is not None and span.page_number is not None
            else "Page unavailable"
        )
        story.append(paragraph(f'"{quote}"\n{page}', "evidence"))
    source_by_id = {source.source_id: source for source in report.external_sources}
    for citation in content.external_citations:
        source = source_by_id.get(citation.source_id)
        locator = source.locator if source is not None else citation.source_id
        story.append(
            paragraph(f'"{citation.quote}"\nExternal snapshot: {locator}', "evidence")
        )


def _append_evidence_delta(
    story,
    *,
    model,
    reviewed,
    model_validation,
    reviewed_validation,
    report,
    paragraph,
) -> None:
    added_quotes = tuple(
        quote for quote in reviewed.evidence_quotes if quote not in model.evidence_quotes
    )
    removed_quotes = tuple(
        quote for quote in model.evidence_quotes if quote not in reviewed.evidence_quotes
    )
    added_external = tuple(
        citation
        for citation in reviewed.external_citations
        if citation not in model.external_citations
    )
    removed_external = tuple(
        citation
        for citation in model.external_citations
        if citation not in reviewed.external_citations
    )
    if not (added_quotes or removed_quotes or added_external or removed_external):
        story.append(paragraph("No citation changes.", "small"))
        return
    if added_quotes or added_external:
        story.append(paragraph("Added evidence", "label"))
        _append_evidence(
            story,
            _EvidenceContent(added_quotes, added_external),
            reviewed_validation,
            report,
            paragraph,
        )
    if removed_quotes or removed_external:
        story.append(paragraph("Removed evidence", "label"))
        _append_evidence(
            story,
            _EvidenceContent(removed_quotes, removed_external),
            model_validation,
            report,
            paragraph,
        )


class _EvidenceContent:
    def __init__(
        self,
        evidence_quotes: tuple[str, ...],
        external_citations: tuple[ExternalCitation, ...],
    ) -> None:
        self.evidence_quotes = evidence_quotes
        self.external_citations = external_citations


def _evidence_reference_text(
    content: TraceableContent,
    validation: ContentValidation | None,
    report: ReviewReport,
) -> str:
    references: list[str] = []
    if validation is not None:
        pages = sorted(
            {
                span.page_number
                for span in validation.evidence_spans
                if span.page_number is not None
            }
        )
        if pages:
            references.append(
                "Source evidence: " + ", ".join(f"page {page}" for page in pages)
            )
    if content.external_citations:
        source_by_id = {source.source_id: source for source in report.external_sources}
        external = [
            source_by_id.get(citation.source_id).title
            if source_by_id.get(citation.source_id) is not None
            else citation.source_id
            for citation in content.external_citations
        ]
        references.append("External snapshots: " + ", ".join(dict.fromkeys(external)))
    return "; ".join(references) or "Evidence reference unavailable"


def _collect_list_evidence(
    evidence,
    *,
    section,
    item_number,
    item,
    model_validation,
    reviewed_validation,
    report,
) -> None:
    candidates = []
    if item.model_item is not None:
        role = (
            "shared"
            if item.status is ListItemReviewStatus.APPROVED_AS_GENERATED
            else "model"
        )
        candidates.append((role, item.model_item, model_validation))
    if (
        item.reviewed_item is not None
        and item.status is not ListItemReviewStatus.APPROVED_AS_GENERATED
    ):
        candidates.append(("human", item.reviewed_item, reviewed_validation))
    source_by_id = {source.source_id: source for source in report.external_sources}
    for role, content, validation in candidates:
        if validation is None:
            continue
        for quote in content.evidence_quotes:
            span = next(
                (span for span in validation.evidence_spans if span.quote == quote),
                None,
            )
            locator = (
                f"Page {span.page_number}"
                if span is not None and span.page_number is not None
                else "Page unavailable"
            )
            evidence.append(
                (
                    _list_evidence_label(section, item_number, role),
                    quote,
                    locator,
                )
            )
        for citation in content.external_citations:
            source = source_by_id.get(citation.source_id)
            locator = (
                f"External snapshot: {source.locator}"
                if source is not None
                else f"External snapshot: {citation.source_id}"
            )
            evidence.append(
                (
                    _list_evidence_label(section, item_number, role),
                    citation.quote,
                    locator,
                )
            )


def _list_evidence_label(section, item_number, role) -> str:
    base = f"{_section_label(section)} item {item_number}"
    return base if role == "shared" else f"{base} ({role})"


def _deduplicate_evidence(entries):
    grouped: dict[tuple[str, str], tuple[list[str], str]] = {}
    for label, quote, locator in entries:
        key = (quote, locator)
        labels, stored_locator = grouped.setdefault(key, ([], locator))
        if label not in labels:
            labels.append(label)
    return [
        ("; ".join(labels), quote, locator)
        for (quote, _), (labels, locator) in grouped.items()
    ]


def _section_status_counts(artifact):
    counts = {status: 0 for status in SectionReviewStatus}
    for section in ALL_SECTIONS:
        review = artifact.sections.get(section)
        if review is not None:
            counts[review.status] += 1
    return counts


def _section_label(section: DossierSection) -> str:
    return {
        DossierSection.PRODUCT_INTENT: "Product intent",
        DossierSection.COMPONENT_FUNCTION: "Component function",
        DossierSection.PERFORMANCE_TO_VALIDATE: "Performance to validate",
        DossierSection.MATERIAL_RELEVANT_CRITERIA: "Material-relevant criteria",
        DossierSection.MISSING_INFORMATION: "Missing information",
        DossierSection.CONFLICTS_AND_TRADEOFFS: "Conflicts and trade-offs",
    }[section]


def _format_timestamp(value) -> str:
    utc = value.astimezone(UTC)
    return f"{utc.day} {utc:%B %Y, %H:%M UTC}"


def _short_hash(value: str) -> str:
    return f"{value[:12]}...{value[-8:]}"


def _human_status(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _safe(value: str) -> str:
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    return escape(clean).replace("\n", "<br/>")
