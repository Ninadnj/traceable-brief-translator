from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pypdf import PdfReader

from compoundx.schemas import (
    CriticAssessment,
    DossierSection,
    ReviewReport,
    SectionFinding,
    TranslationSection,
)
from compoundx.section_review import (
    ALL_SECTIONS,
    LIST_SECTIONS,
    NARRATIVE_SECTIONS,
    ListItemReview,
    ListItemReviewStatus,
    ListSectionReview,
    NarrativeSectionReview,
    OverallReviewStatus,
    SavedSectionReview,
    SectionReviewStatus,
    build_approved_list_review,
    build_approved_narrative_review,
    finalize_section_review,
    load_accepted_report,
    save_section_review,
)


# The garden-trimmer report carries both narrative and list-section revision
# requests, so it exercises the complete flagged-section path.
ACCEPTED_TRIMMER_REPORT = (
    Path(__file__).parents[1]
    / "demo-results"
    / "garden-trimmer"
    / "acceptance"
    / "report.json"
)
REVIEWER = "reviewer-01"
START = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
NARRATIVE_RATIONALE = (
    "Fold the critic-cited field observations back in as context for validation, "
    "without turning them into acceptance limits."
)
LIST_RATIONALE = "Adopt the critic's additional unresolved point after checking its citations."


def _copy_accepted_report(tmp_path: Path) -> Path:
    path = tmp_path / "run" / "acceptance" / "report.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(ACCEPTED_TRIMMER_REPORT.read_bytes())
    return path


def _flagged_findings(report: ReviewReport) -> tuple[SectionFinding, ...]:
    assert report.critic is not None
    return tuple(
        finding for finding in report.critic.findings if finding.human_review_required
    )


def _flagged_finding(report: ReviewReport, sections: tuple[DossierSection, ...]):
    return next(
        finding for finding in _flagged_findings(report) if finding.section in sections
    )


def _corrected_narrative(
    report: ReviewReport,
    finding: SectionFinding,
) -> tuple[NarrativeSectionReview, tuple[str, ...]]:
    """Apply the critic's own verified quotes so the correction stays provable."""

    model: TranslationSection = getattr(report.translation, finding.section.value)
    added_quotes = tuple(
        quote
        for quote in finding.evidence_quotes
        if quote not in model.evidence_quotes
    )
    assert added_quotes
    reviewed = model.model_copy(
        update={
            "text": (
                f"{model.text} Field evidence retained as context: "
                + " ".join(added_quotes)
            ),
            "evidence_quotes": model.evidence_quotes + added_quotes,
        }
    )
    review = NarrativeSectionReview(
        section=finding.section,
        status=SectionReviewStatus.CORRECTED,
        model_content=model,
        reviewed_content=reviewed,
        rationale=NARRATIVE_RATIONALE,
    )
    return review, added_quotes


def _corrected_list(report: ReviewReport, finding: SectionFinding) -> ListSectionReview:
    """Accept the critic's proposed extra point as a human-owned addition."""

    assert report.critic is not None
    model_points = getattr(report.translation, finding.section.value)
    added_point = report.critic.additional_missing_information[0]
    items = [
        ListItemReview(
            status=ListItemReviewStatus.APPROVED_AS_GENERATED,
            model_index=index,
            model_item=point,
            reviewed_item=point,
        )
        for index, point in enumerate(model_points)
    ]
    items.append(
        ListItemReview(
            status=ListItemReviewStatus.ADDED,
            reviewed_item=added_point,
            rationale=LIST_RATIONALE,
        )
    )
    return ListSectionReview(
        section=finding.section,
        status=SectionReviewStatus.CORRECTED,
        items=tuple(items),
    )


def _all_section_reviews(
    report: ReviewReport,
    overrides: dict[DossierSection, NarrativeSectionReview | ListSectionReview],
) -> list[NarrativeSectionReview | ListSectionReview]:
    return [
        overrides.get(section)
        or (
            build_approved_narrative_review(report, section)
            if section in NARRATIVE_SECTIONS
            else build_approved_list_review(report, section)
        )
        for section in ALL_SECTIONS
    ]


def _save_chain(
    report_path: Path,
    reviews: list[NarrativeSectionReview | ListSectionReview],
) -> SavedSectionReview:
    parent: Path | None = None
    saved: SavedSectionReview | None = None
    for index, review in enumerate(reviews):
        saved = save_section_review(
            accepted_report_path=report_path,
            reviewer_id=REVIEWER,
            review=review,
            parent_review_path=parent,
            created_at=START + timedelta(minutes=index),
        )
        parent = saved.json_path
    assert saved is not None
    return saved


def _owner_validation(artifact, owner: str):
    return next(
        content for content in artifact.validation.contents if content.owner == owner
    )


def test_accepted_report_carries_critic_findings_that_demand_human_review() -> None:
    report, _ = load_accepted_report(ACCEPTED_TRIMMER_REPORT)
    assert report.critic is not None

    flagged = {finding.section for finding in _flagged_findings(report)}
    revise = {
        finding.section
        for finding in report.critic.findings
        if finding.assessment is CriticAssessment.REVISE
    }

    assert flagged == {
        DossierSection.PERFORMANCE_TO_VALIDATE,
        DossierSection.MATERIAL_RELEVANT_CRITERIA,
        DossierSection.MISSING_INFORMATION,
    }
    assert revise == flagged
    assert len(flagged & set(NARRATIVE_SECTIONS)) == 2
    assert len(flagged & set(LIST_SECTIONS)) == 1
    for finding in _flagged_findings(report):
        assert finding.suggested_revision
        assert finding.evidence_quotes
    assert len(report.critic.additional_missing_information) == 1
    assert len(report.critic.additional_open_questions) == 0
    assert report.critic_validation is not None
    assert report.critic_validation.errors == ()


def test_correction_to_a_flagged_narrative_section_is_recorded_and_revalidates(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    finding = _flagged_finding(report, NARRATIVE_SECTIONS)
    correction, added_quotes = _corrected_narrative(report, finding)
    first = save_section_review(
        accepted_report_path=report_path,
        reviewer_id=REVIEWER,
        review=build_approved_narrative_review(report, DossierSection.PRODUCT_INTENT),
        created_at=START,
    )

    saved = save_section_review(
        accepted_report_path=report_path,
        reviewer_id=REVIEWER,
        review=correction,
        parent_review_path=first.json_path,
        created_at=START + timedelta(minutes=1),
    )

    recorded = saved.artifact.sections.get(finding.section)
    assert isinstance(recorded, NarrativeSectionReview)
    assert recorded.status is SectionReviewStatus.CORRECTED
    assert recorded.rationale == NARRATIVE_RATIONALE
    assert recorded.reviewed_content != recorded.model_content
    assert saved.artifact.updated_section is finding.section
    assert saved.artifact.parent_review is not None
    assert saved.artifact.parent_review.sha256 == hashlib.sha256(
        first.json_path.read_bytes()
    ).hexdigest()
    assert saved.artifact.parent_review.relative_path == first.json_path.relative_to(
        report_path.parents[1]
    ).as_posix()
    assert getattr(
        saved.artifact.reviewed_dossier, finding.section.value
    ) == recorded.reviewed_content

    assert saved.artifact.validation.invalid_content_count == 0
    validation = _owner_validation(saved.artifact, finding.section.value)
    assert validation.valid
    resolved = {span.quote for span in validation.evidence_spans}
    assert set(added_quotes) <= resolved
    assert all(span.page_number is not None for span in validation.evidence_spans)
    markdown = saved.markdown_path.read_text(encoding="utf-8")
    assert NARRATIVE_RATIONALE in markdown
    assert "Reason for correction" in markdown


def test_flagged_list_section_can_adopt_the_critic_proposed_point(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    assert report.critic is not None
    finding = _flagged_finding(report, LIST_SECTIONS)
    model_points = getattr(report.translation, finding.section.value)
    added_point = report.critic.additional_missing_information[0]

    saved = save_section_review(
        accepted_report_path=report_path,
        reviewer_id=REVIEWER,
        review=_corrected_list(report, finding),
        created_at=START,
    )

    reviewed_points = getattr(saved.artifact.reviewed_dossier, finding.section.value)
    assert len(reviewed_points) == len(model_points) + 1
    assert reviewed_points[-1] == added_point
    assert saved.artifact.validation.invalid_content_count == 0
    assert _owner_validation(
        saved.artifact,
        f"{finding.section.value}[{len(reviewed_points)}]",
    ).valid
    assert LIST_RATIONALE in saved.markdown_path.read_text(encoding="utf-8")


def test_finalizing_resolved_flags_derives_approved_with_corrections(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    narrative_finding = _flagged_finding(report, NARRATIVE_SECTIONS)
    list_finding = _flagged_finding(report, LIST_SECTIONS)
    correction, _ = _corrected_narrative(report, narrative_finding)
    parent = _save_chain(
        report_path,
        _all_section_reviews(
            report,
            {
                narrative_finding.section: correction,
                list_finding.section: _corrected_list(report, list_finding),
            },
        ),
    )

    final = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id=REVIEWER,
        parent_review_path=parent.json_path,
        decision_rationale="Both critic-flagged sections were corrected and revalidated.",
        created_at=START + timedelta(minutes=10),
    )

    assert final.artifact.overall_status is OverallReviewStatus.APPROVED_WITH_CORRECTIONS
    assert final.artifact.validation.invalid_content_count == 0
    markdown = final.markdown_path.read_text(encoding="utf-8")
    for section in (narrative_finding.section, list_finding.section):
        label = section.value.replace("_", " ")
        assert f"- {label}: Corrected" in markdown
    assert final.pdf_path is not None
    pdf_text = " ".join(
        " ".join(page.extract_text() or "" for page in PdfReader(final.pdf_path).pages).split()
    )
    assert "Model critic assessment (before human review)" in pdf_text
    assert NARRATIVE_RATIONALE in pdf_text
    assert LIST_RATIONALE in pdf_text


def test_flagged_section_may_be_approved_without_a_rationale(tmp_path: Path) -> None:
    """The lifecycle has no gate on adverse critic findings; only rendering carries them."""

    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    narrative_finding = _flagged_finding(report, NARRATIVE_SECTIONS)
    parent = _save_chain(report_path, _all_section_reviews(report, {}))

    recorded = parent.artifact.sections.get(narrative_finding.section)
    assert isinstance(recorded, NarrativeSectionReview)
    assert recorded.status is SectionReviewStatus.APPROVED_AS_GENERATED
    assert recorded.rationale is None
    assert recorded.reviewer_note is None

    final = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id=REVIEWER,
        parent_review_path=parent.json_path,
        decision_rationale="Accepted as generated despite the critic's revision requests.",
        created_at=START + timedelta(minutes=10),
    )

    assert final.artifact.overall_status is OverallReviewStatus.APPROVED_AS_GENERATED
    markdown = final.markdown_path.read_text(encoding="utf-8")
    assert f"**Critic assessment:** {CriticAssessment.REVISE.value}" in markdown
    assert NARRATIVE_RATIONALE not in markdown
