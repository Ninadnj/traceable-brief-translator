from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader
from pydantic import ValidationError

from compoundx.schemas import DossierSection
from compoundx.section_review import (
    ALL_SECTIONS,
    LIST_SECTIONS,
    NARRATIVE_SECTIONS,
    ListItemReview,
    ListItemReviewStatus,
    ListSectionReview,
    NarrativeSectionReview,
    OverallReviewStatus,
    SectionReviewLifecycleError,
    SectionReviewStatus,
    build_approved_list_review,
    build_approved_narrative_review,
    finalize_section_review,
    load_accepted_report,
    load_section_review,
    save_section_review,
)


ACCEPTED_ROLLER_REPORT = (
    Path(__file__).parents[1]
    / "demo-results"
    / "roller-skate"
    / "acceptance"
    / "report.json"
)
START = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)


def _copy_accepted_report(tmp_path: Path) -> Path:
    path = tmp_path / "run" / "acceptance" / "report.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(ACCEPTED_ROLLER_REPORT.read_bytes())
    return path


def _save_all_approved(report_path: Path):
    report, _ = load_accepted_report(report_path)
    parent = None
    saved = None
    for index, section in enumerate(ALL_SECTIONS):
        review = (
            build_approved_narrative_review(report, section)
            if section in NARRATIVE_SECTIONS
            else build_approved_list_review(report, section)
        )
        saved = save_section_review(
            accepted_report_path=report_path,
            reviewer_id="reviewer-01",
            review=review,
            parent_review_path=parent,
            created_at=START + timedelta(minutes=index),
        )
        parent = saved.json_path
    assert saved is not None
    return saved


def test_approved_narrative_must_preserve_every_model_field(
    tmp_path: Path,
) -> None:
    report, _ = load_accepted_report(_copy_accepted_report(tmp_path))
    model = report.translation.product_intent
    changed = model.model_copy(update={"text": model.text + " Clarified."})

    with pytest.raises(ValidationError, match="preserve all model content"):
        NarrativeSectionReview(
            section=DossierSection.PRODUCT_INTENT,
            status=SectionReviewStatus.APPROVED_AS_GENERATED,
            model_content=model,
            reviewed_content=changed,
        )


def test_corrected_and_unresolved_narratives_require_explanations(
    tmp_path: Path,
) -> None:
    report, _ = load_accepted_report(_copy_accepted_report(tmp_path))
    model = report.translation.product_intent
    changed = model.model_copy(update={"text": model.text + " Clarified."})

    with pytest.raises(ValidationError, match="requires a rationale"):
        NarrativeSectionReview(
            section=DossierSection.PRODUCT_INTENT,
            status=SectionReviewStatus.CORRECTED,
            model_content=model,
            reviewed_content=changed,
        )
    with pytest.raises(ValidationError, match="require a reviewer note"):
        NarrativeSectionReview(
            section=DossierSection.PRODUCT_INTENT,
            status=SectionReviewStatus.NEEDS_FURTHER_REVIEW,
            model_content=model,
            reviewed_content=model,
        )


def test_list_item_mutations_require_rationales(tmp_path: Path) -> None:
    report, _ = load_accepted_report(_copy_accepted_report(tmp_path))
    model_item = report.translation.missing_information[0]

    with pytest.raises(ValidationError, match="removed list item requires"):
        ListItemReview(
            status=ListItemReviewStatus.REMOVED,
            model_index=0,
            model_item=model_item,
        )
    with pytest.raises(ValidationError, match="added list item requires a rationale"):
        ListItemReview(
            status=ListItemReviewStatus.ADDED,
            reviewed_item=model_item,
        )


def test_each_section_save_is_immutable_and_parent_linked(tmp_path: Path) -> None:
    report_path = _copy_accepted_report(tmp_path)
    original = report_path.read_bytes()
    report, _ = load_accepted_report(report_path)
    first = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=build_approved_narrative_review(
            report,
            DossierSection.PRODUCT_INTENT,
        ),
        created_at=START,
    )
    second = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=build_approved_narrative_review(
            report,
            DossierSection.COMPONENT_FUNCTION,
        ),
        parent_review_path=first.json_path,
        created_at=START + timedelta(minutes=1),
    )

    assert first.json_path.is_file()
    assert second.artifact.parent_review is not None
    assert second.artifact.parent_review.sha256 == hashlib.sha256(
        first.json_path.read_bytes()
    ).hexdigest()
    assert report_path.read_bytes() == original
    assert load_section_review(second.json_path) == second.artifact


def test_list_review_must_account_for_every_model_item_once(tmp_path: Path) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    valid = build_approved_list_review(
        report,
        DossierSection.MISSING_INFORMATION,
    )
    incomplete = valid.model_copy(update={"items": valid.items[1:]})

    with pytest.raises(SectionReviewLifecycleError, match="every model item"):
        save_section_review(
            accepted_report_path=report_path,
            reviewer_id="reviewer-01",
            review=incomplete,
            created_at=START,
        )


def test_list_section_supports_correction_removal_and_human_addition(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    model_points = report.translation.missing_information
    corrected_point = model_points[0].model_copy(
        update={"text": model_points[0].text + " Record the comparison method."}
    )
    added_point = model_points[0].model_copy(
        update={"text": "Document the human-reviewed comparison method."}
    )
    items = [
        ListItemReview(
            status=ListItemReviewStatus.CORRECTED,
            model_index=0,
            model_item=model_points[0],
            reviewed_item=corrected_point,
            rationale="Make the requested comparison method explicit.",
        ),
        ListItemReview(
            status=ListItemReviewStatus.REMOVED,
            model_index=1,
            model_item=model_points[1],
            rationale="This item is outside the bounded review question.",
        ),
    ]
    items.extend(
        ListItemReview(
            status=ListItemReviewStatus.APPROVED_AS_GENERATED,
            model_index=index,
            model_item=model_points[index],
            reviewed_item=model_points[index],
        )
        for index in range(2, len(model_points))
    )
    items.append(
        ListItemReview(
            status=ListItemReviewStatus.ADDED,
            reviewed_item=added_point,
            rationale="Capture an omitted documentation need.",
        )
    )
    review = ListSectionReview(
        section=DossierSection.MISSING_INFORMATION,
        status=SectionReviewStatus.CORRECTED,
        items=tuple(items),
    )

    saved = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=review,
        created_at=START,
    )

    assert len(saved.artifact.reviewed_dossier.missing_information) == len(
        model_points
    )
    assert saved.artifact.reviewed_dossier.missing_information[0] == corrected_point
    assert saved.artifact.reviewed_dossier.missing_information[-1] == added_point
    assert saved.artifact.validation.invalid_content_count == 0


def test_corrected_section_derives_approved_with_corrections(tmp_path: Path) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    model = report.translation.product_intent
    corrected = NarrativeSectionReview(
        section=DossierSection.PRODUCT_INTENT,
        status=SectionReviewStatus.CORRECTED,
        model_content=model,
        reviewed_content=model.model_copy(
            update={"text": model.text + " Reliability remains the governing priority."}
        ),
        rationale="Make the priority hierarchy explicit.",
    )
    parent = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=corrected,
        created_at=START,
    )
    for index, section in enumerate(ALL_SECTIONS[1:], start=1):
        review = (
            build_approved_narrative_review(report, section)
            if section in NARRATIVE_SECTIONS
            else build_approved_list_review(report, section)
        )
        parent = save_section_review(
            accepted_report_path=report_path,
            reviewer_id="reviewer-01",
            review=review,
            parent_review_path=parent.json_path,
            created_at=START + timedelta(minutes=index),
        )

    final = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        parent_review_path=parent.json_path,
        decision_rationale="All six sections have been reviewed.",
        created_at=START + timedelta(minutes=10),
    )

    assert final.artifact.overall_status is (
        OverallReviewStatus.APPROVED_WITH_CORRECTIONS
    )
    assert final.artifact.validation.invalid_content_count == 0
    assert final.pdf_path is not None
    pages = [page.extract_text() or "" for page in PdfReader(final.pdf_path).pages]
    assert "1. Product intent" in pages[1]
    assert "Human review" in pages[2]
    assert "2. Component function" not in pages[2]
    assert "2. Component function" in pages[3]


def test_unresolved_section_and_mechanical_error_each_block_approval(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    model = report.translation.product_intent
    unresolved = NarrativeSectionReview(
        section=DossierSection.PRODUCT_INTENT,
        status=SectionReviewStatus.NEEDS_FURTHER_REVIEW,
        model_content=model,
        reviewed_content=model,
        reviewer_note="Acceptance criteria need an engineering decision.",
    )
    saved = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=unresolved,
        created_at=START,
    )
    assert saved.artifact.overall_status is OverallReviewStatus.NEEDS_FURTHER_REVIEW

    invalid = NarrativeSectionReview(
        section=DossierSection.PRODUCT_INTENT,
        status=SectionReviewStatus.CORRECTED,
        model_content=model,
        reviewed_content=model.model_copy(
            update={"text": model.text + " The target is 999 MPa."}
        ),
        rationale="Record a proposed target.",
    )
    invalid_saved = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=invalid,
        created_at=START + timedelta(minutes=1),
    )
    assert invalid_saved.artifact.validation.invalid_content_count == 1
    assert invalid_saved.artifact.overall_status is (
        OverallReviewStatus.NEEDS_FURTHER_REVIEW
    )


def test_finalization_requires_all_six_section_decisions(tmp_path: Path) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    partial = save_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        review=build_approved_narrative_review(
            report,
            DossierSection.PRODUCT_INTENT,
        ),
        created_at=START,
    )

    with pytest.raises(SectionReviewLifecycleError, match="all six sections"):
        finalize_section_review(
            accepted_report_path=report_path,
            reviewer_id="reviewer-01",
            parent_review_path=partial.json_path,
            decision_rationale="Premature finalization.",
            created_at=START + timedelta(minutes=1),
        )


def test_final_review_writes_combined_markdown_pdf_and_hash_manifest(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    parent = _save_all_approved(report_path)
    final = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        parent_review_path=parent.json_path,
        decision_rationale="All sections are supported as generated.",
        created_at=START + timedelta(minutes=10),
    )

    assert final.artifact.overall_status is OverallReviewStatus.APPROVED_AS_GENERATED
    assert final.pdf_path is not None and final.pdf_path.read_bytes().startswith(b"%PDF")
    assert final.manifest_path is not None and final.manifest_path.is_file()
    markdown = final.markdown_path.read_text(encoding="utf-8")
    assert "Model-generated translation" in markdown
    assert "Human-reviewed translation" in markdown
    assert "Verified source evidence" in markdown
    assert "Page 1" in markdown
    manifest = final.manifest_path.read_text(encoding="utf-8")
    assert hashlib.sha256(final.pdf_path.read_bytes()).hexdigest() in manifest

    report, _ = load_accepted_report(report_path)
    reader = PdfReader(final.pdf_path)
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) <= 12
    assert "Model critic assessment (before human review)" in pdf_text
    assert "Reviewed JSON SHA-256" in pdf_text
    assert "Reviewed artifact SHA-256" not in pdf_text
    assert "No changes made." in pdf_text
    compact_pdf_text = " ".join(pdf_text.split())
    component_text = " ".join(report.translation.component_function.text.split())
    missing_text = " ".join(report.translation.missing_information[0].text.split())
    critic_excerpts = tuple(
        " ".join(excerpt.split())
        for finding in (report.critic.findings if report.critic else ())
        for excerpt in finding.translated_excerpts
    )
    assert compact_pdf_text.count(component_text) == 1 + critic_excerpts.count(
        component_text
    )
    assert compact_pdf_text.count(missing_text) == 1 + critic_excerpts.count(
        missing_text
    )


def test_explicit_rejection_is_the_only_overall_status_override(
    tmp_path: Path,
) -> None:
    report_path = _copy_accepted_report(tmp_path)
    parent = _save_all_approved(report_path)
    rejected = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        parent_review_path=parent.json_path,
        decision_rationale="The complete dossier is unsuitable for decision use.",
        reject=True,
        created_at=START + timedelta(minutes=10),
    )

    assert rejected.artifact.overall_status is OverallReviewStatus.REJECTED


def test_terminal_section_review_cannot_be_continued(tmp_path: Path) -> None:
    report_path = _copy_accepted_report(tmp_path)
    report, _ = load_accepted_report(report_path)
    parent = _save_all_approved(report_path)
    final = finalize_section_review(
        accepted_report_path=report_path,
        reviewer_id="reviewer-01",
        parent_review_path=parent.json_path,
        decision_rationale="Finalize the complete review.",
        created_at=START + timedelta(minutes=10),
    )

    with pytest.raises(SectionReviewLifecycleError, match="terminal"):
        save_section_review(
            accepted_report_path=report_path,
            reviewer_id="reviewer-01",
            review=build_approved_narrative_review(
                report,
                DossierSection.PRODUCT_INTENT,
            ),
            parent_review_path=final.json_path,
            created_at=START + timedelta(minutes=11),
        )
