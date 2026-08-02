"""Section-level human review over an immutable accepted report."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator

from compoundx.artifact_io import model_bytes, write_bytes, write_model
from compoundx.models import CompoundXModel, SourceDocument
from compoundx.schemas import (
    DossierSection,
    DossierValidation,
    ReviewReport,
    TechnicalDossier,
    TraceablePoint,
    TranslationSection,
)
from compoundx.verifier import verify_critic, verify_dossier


NARRATIVE_SECTIONS = (
    DossierSection.PRODUCT_INTENT,
    DossierSection.COMPONENT_FUNCTION,
    DossierSection.PERFORMANCE_TO_VALIDATE,
    DossierSection.MATERIAL_RELEVANT_CRITERIA,
)
LIST_SECTIONS = (
    DossierSection.MISSING_INFORMATION,
    DossierSection.CONFLICTS_AND_TRADEOFFS,
)
ALL_SECTIONS = (*NARRATIVE_SECTIONS, *LIST_SECTIONS)


class SectionReviewStatus(str, Enum):
    APPROVED_AS_GENERATED = "approved_as_generated"
    CORRECTED = "corrected"
    NEEDS_FURTHER_REVIEW = "needs_further_review"


class ListItemReviewStatus(str, Enum):
    APPROVED_AS_GENERATED = "approved_as_generated"
    CORRECTED = "corrected"
    REMOVED = "removed"
    ADDED = "added"


class OverallReviewStatus(str, Enum):
    APPROVED_AS_GENERATED = "approved_as_generated"
    APPROVED_WITH_CORRECTIONS = "approved_with_corrections"
    NEEDS_FURTHER_REVIEW = "needs_further_review"
    REJECTED = "rejected"


class SectionReviewAction(str, Enum):
    SAVE_SECTION = "save_section"
    FINALIZE = "finalize"
    REJECT = "reject"


class NarrativeSectionReview(CompoundXModel):
    section: DossierSection
    status: SectionReviewStatus
    model_content: TranslationSection
    reviewed_content: TranslationSection
    rationale: str | None = Field(default=None, min_length=1)
    reviewer_note: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> NarrativeSectionReview:
        if self.section not in NARRATIVE_SECTIONS:
            raise ValueError("narrative review requires a narrative dossier section")
        changed = self.reviewed_content != self.model_content
        if self.status is SectionReviewStatus.APPROVED_AS_GENERATED:
            if changed:
                raise ValueError(
                    "approved-as-generated section must preserve all model content"
                )
        elif self.status is SectionReviewStatus.CORRECTED:
            if not changed:
                raise ValueError("corrected section must contain an actual change")
            if not self.rationale or not self.rationale.strip():
                raise ValueError("corrected section requires a rationale")
        else:
            if not self.reviewer_note or not self.reviewer_note.strip():
                raise ValueError(
                    "sections needing further review require a reviewer note"
                )
            if changed and (not self.rationale or not self.rationale.strip()):
                raise ValueError(
                    "provisional changes in an unresolved section require a rationale"
                )
        return self


class ListItemReview(CompoundXModel):
    status: ListItemReviewStatus
    model_index: int | None = Field(default=None, ge=0)
    model_item: TraceablePoint | None = None
    reviewed_item: TraceablePoint | None = None
    rationale: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> ListItemReview:
        has_model = self.model_index is not None and self.model_item is not None
        has_reviewed = self.reviewed_item is not None
        has_rationale = bool(self.rationale and self.rationale.strip())
        if self.status is ListItemReviewStatus.APPROVED_AS_GENERATED:
            if not has_model or not has_reviewed:
                raise ValueError("approved list item requires model and reviewed content")
            if self.reviewed_item != self.model_item:
                raise ValueError(
                    "approved-as-generated list item must preserve model content"
                )
        elif self.status is ListItemReviewStatus.CORRECTED:
            if not has_model or not has_reviewed:
                raise ValueError("corrected list item requires model and reviewed content")
            if self.reviewed_item == self.model_item:
                raise ValueError("corrected list item must contain an actual change")
            if not has_rationale:
                raise ValueError("corrected list item requires a rationale")
        elif self.status is ListItemReviewStatus.REMOVED:
            if not has_model or has_reviewed:
                raise ValueError("removed list item keeps only its model content")
            if not has_rationale:
                raise ValueError("removed list item requires a rationale")
        else:
            if self.model_index is not None or self.model_item is not None:
                raise ValueError("added list item cannot refer to model content")
            if not has_reviewed:
                raise ValueError("added list item requires reviewed content")
            if not has_rationale:
                raise ValueError("added list item requires a rationale")
        return self


class ListSectionReview(CompoundXModel):
    section: DossierSection
    status: SectionReviewStatus
    items: tuple[ListItemReview, ...] = Field(min_length=1)
    reviewer_note: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> ListSectionReview:
        if self.section not in LIST_SECTIONS:
            raise ValueError("list review requires a list dossier section")
        changed = any(
            item.status is not ListItemReviewStatus.APPROVED_AS_GENERATED
            for item in self.items
        )
        if self.status is SectionReviewStatus.APPROVED_AS_GENERATED and changed:
            raise ValueError(
                "approved-as-generated list section cannot contain item changes"
            )
        if self.status is SectionReviewStatus.CORRECTED and not changed:
            raise ValueError("corrected list section requires at least one item change")
        if self.status is SectionReviewStatus.NEEDS_FURTHER_REVIEW:
            if not self.reviewer_note or not self.reviewer_note.strip():
                raise ValueError(
                    "list sections needing further review require a reviewer note"
                )
        return self


class SectionReviewSet(CompoundXModel):
    product_intent: NarrativeSectionReview | None = None
    component_function: NarrativeSectionReview | None = None
    performance_to_validate: NarrativeSectionReview | None = None
    material_relevant_criteria: NarrativeSectionReview | None = None
    missing_information: ListSectionReview | None = None
    conflicts_and_tradeoffs: ListSectionReview | None = None

    def get(
        self,
        section: DossierSection,
    ) -> NarrativeSectionReview | ListSectionReview | None:
        return getattr(self, section.value)

    def with_review(
        self,
        review: NarrativeSectionReview | ListSectionReview,
    ) -> SectionReviewSet:
        return self.model_copy(update={review.section.value: review})

    @property
    def complete(self) -> bool:
        return all(self.get(section) is not None for section in ALL_SECTIONS)


class SectionReviewParent(CompoundXModel):
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SectionReviewArtifact(CompoundXModel):
    schema_version: Literal["compoundx.section-review.v2"]
    created_at: datetime
    reviewer_id: str = Field(min_length=1)
    action: SectionReviewAction
    updated_section: DossierSection | None = None
    decision_rationale: str | None = Field(default=None, min_length=1)
    accepted_report_relative_path: str = Field(min_length=1)
    accepted_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_review: SectionReviewParent | None = None
    sections: SectionReviewSet
    reviewed_dossier: TechnicalDossier
    validation: DossierValidation
    known_limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action(self) -> SectionReviewArtifact:
        if self.action is SectionReviewAction.SAVE_SECTION:
            if self.updated_section is None:
                raise ValueError("section save requires updated_section")
            if self.sections.get(self.updated_section) is None:
                raise ValueError("updated section is missing from the review set")
        else:
            if self.updated_section is not None:
                raise ValueError("terminal review action cannot update one section")
            if not self.sections.complete:
                raise ValueError("final review requires decisions for all six sections")
            if not self.decision_rationale or not self.decision_rationale.strip():
                raise ValueError("final review requires a decision rationale")
        return self

    @computed_field(return_type=OverallReviewStatus)
    @property
    def overall_status(self) -> OverallReviewStatus:
        if self.action is SectionReviewAction.REJECT:
            return OverallReviewStatus.REJECTED
        reviews = tuple(
            self.sections.get(section)
            for section in ALL_SECTIONS
        )
        if (
            any(review is None for review in reviews)
            or any(
                review is not None
                and review.status is SectionReviewStatus.NEEDS_FURTHER_REVIEW
                for review in reviews
            )
            or self.validation.invalid_content_count > 0
        ):
            return OverallReviewStatus.NEEDS_FURTHER_REVIEW
        if any(
            review is not None
            and review.status is SectionReviewStatus.CORRECTED
            for review in reviews
        ):
            return OverallReviewStatus.APPROVED_WITH_CORRECTIONS
        return OverallReviewStatus.APPROVED_AS_GENERATED


class FinalReviewManifest(CompoundXModel):
    schema_version: Literal["compoundx.section-review-manifest.v1"]
    reviewed_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    json_path: str = Field(min_length=1)
    markdown_path: str = Field(min_length=1)
    pdf_path: str = Field(min_length=1)


@dataclass(frozen=True)
class SavedSectionReview:
    artifact: SectionReviewArtifact
    review_dir: Path
    json_path: Path
    markdown_path: Path
    pdf_path: Path | None = None
    manifest_path: Path | None = None


KNOWN_SECTION_REVIEW_LIMITATIONS = (
    "Mechanical revalidation proves exact citation, source-reference and numeric "
    "provenance rules; it does not prove engineering correctness.",
    "The model critic is not rerun over human revisions; any semantic evaluation "
    "is a separate supporting artifact and is not part of this review record.",
    "Approval records one reviewer decision and is not independent multi-reviewer "
    "sign-off.",
)


def load_accepted_report(path: str | Path) -> tuple[ReviewReport, str]:
    report_path = Path(path)
    if report_path.name != "report.json" or report_path.parent.name != "acceptance":
        raise SectionReviewLifecycleError(
            "section review must start from an acceptance/report.json artifact"
        )
    content = report_path.read_bytes()
    report = ReviewReport.model_validate_json(content)
    validation = verify_dossier(
        _source_document(report),
        report.translation,
        report.external_sources,
        report.source_pages,
    )
    if validation.invalid_content_count:
        raise SectionReviewLifecycleError(
            "accepted report fails current deterministic revalidation"
        )
    if report.critic_error is not None or report.critic is None:
        raise SectionReviewLifecycleError(
            "accepted report does not contain a completed critic pass"
        )
    critic_validation = verify_critic(
        _source_document(report),
        report.critic,
        report.external_sources,
        report.source_pages,
        report.translation,
        validation,
        require_current_contract=(
            report.critic.contract_version == "compoundx.critic.v2"
        ),
    )
    if critic_validation.errors:
        raise SectionReviewLifecycleError(
            "accepted report's critic output fails current deterministic revalidation"
        )
    return report, hashlib.sha256(content).hexdigest()


def load_section_review(path: str | Path) -> SectionReviewArtifact:
    return SectionReviewArtifact.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def list_section_reviews(run_dir: str | Path) -> tuple[Path, ...]:
    return tuple(sorted(Path(run_dir).glob("section-reviews/*/review.json")))


def save_section_review(
    *,
    accepted_report_path: str | Path,
    reviewer_id: str,
    review: NarrativeSectionReview | ListSectionReview,
    parent_review_path: str | Path | None = None,
    created_at: datetime | None = None,
) -> SavedSectionReview:
    context = _load_context(
        accepted_report_path=accepted_report_path,
        reviewer_id=reviewer_id,
        parent_review_path=parent_review_path,
    )
    sections = context.sections.with_review(review)
    _validate_reviews_against_report(sections, context.report)
    dossier = reconstruct_reviewed_dossier(context.report, sections)
    validation = verify_dossier(
        _source_document(context.report),
        dossier,
        context.report.external_sources,
        context.report.source_pages,
    )
    artifact = SectionReviewArtifact(
        schema_version="compoundx.section-review.v2",
        created_at=created_at or datetime.now(UTC),
        reviewer_id=context.reviewer_id,
        action=SectionReviewAction.SAVE_SECTION,
        updated_section=review.section,
        accepted_report_relative_path=context.report_relative_path,
        accepted_report_sha256=context.report_sha256,
        source_sha256=context.report.source.sha256,
        parent_review=context.parent,
        sections=sections,
        reviewed_dossier=dossier,
        validation=validation,
        known_limitations=KNOWN_SECTION_REVIEW_LIMITATIONS,
    )
    return _write_artifact(context, artifact, final=False)


def finalize_section_review(
    *,
    accepted_report_path: str | Path,
    reviewer_id: str,
    parent_review_path: str | Path,
    decision_rationale: str,
    reject: bool = False,
    created_at: datetime | None = None,
) -> SavedSectionReview:
    context = _load_context(
        accepted_report_path=accepted_report_path,
        reviewer_id=reviewer_id,
        parent_review_path=parent_review_path,
    )
    if not context.sections.complete:
        raise SectionReviewLifecycleError(
            "final review requires a decision for all six sections"
        )
    _validate_reviews_against_report(context.sections, context.report)
    dossier = reconstruct_reviewed_dossier(context.report, context.sections)
    validation = verify_dossier(
        _source_document(context.report),
        dossier,
        context.report.external_sources,
        context.report.source_pages,
    )
    artifact = SectionReviewArtifact(
        schema_version="compoundx.section-review.v2",
        created_at=created_at or datetime.now(UTC),
        reviewer_id=context.reviewer_id,
        action=(SectionReviewAction.REJECT if reject else SectionReviewAction.FINALIZE),
        decision_rationale=decision_rationale.strip(),
        accepted_report_relative_path=context.report_relative_path,
        accepted_report_sha256=context.report_sha256,
        source_sha256=context.report.source.sha256,
        parent_review=context.parent,
        sections=context.sections,
        reviewed_dossier=dossier,
        validation=validation,
        known_limitations=KNOWN_SECTION_REVIEW_LIMITATIONS,
    )
    return _write_artifact(context, artifact, final=True)


def reconstruct_reviewed_dossier(
    report: ReviewReport,
    sections: SectionReviewSet,
) -> TechnicalDossier:
    values: dict[str, object] = {}
    for section in NARRATIVE_SECTIONS:
        review = sections.get(section)
        values[section.value] = (
            review.reviewed_content
            if isinstance(review, NarrativeSectionReview)
            else getattr(report.translation, section.value)
        )
    for section in LIST_SECTIONS:
        review = sections.get(section)
        values[section.value] = (
            _reviewed_list(review)
            if isinstance(review, ListSectionReview)
            else getattr(report.translation, section.value)
        )
    try:
        return TechnicalDossier.model_validate(values)
    except ValueError as error:
        raise SectionReviewLifecycleError(
            f"reviewed dossier is structurally invalid: {error}"
        ) from error


def build_approved_narrative_review(
    report: ReviewReport,
    section: DossierSection,
) -> NarrativeSectionReview:
    if section not in NARRATIVE_SECTIONS:
        raise ValueError("section is not narrative")
    content = getattr(report.translation, section.value)
    return NarrativeSectionReview(
        section=section,
        status=SectionReviewStatus.APPROVED_AS_GENERATED,
        model_content=content,
        reviewed_content=content,
    )


def build_approved_list_review(
    report: ReviewReport,
    section: DossierSection,
) -> ListSectionReview:
    if section not in LIST_SECTIONS:
        raise ValueError("section is not list-based")
    points = getattr(report.translation, section.value)
    return ListSectionReview(
        section=section,
        status=SectionReviewStatus.APPROVED_AS_GENERATED,
        items=tuple(
            ListItemReview(
                status=ListItemReviewStatus.APPROVED_AS_GENERATED,
                model_index=index,
                model_item=point,
                reviewed_item=point,
            )
            for index, point in enumerate(points)
        ),
    )


def render_combined_markdown(
    artifact: SectionReviewArtifact,
    report: ReviewReport,
    reviewed_artifact_sha256: str,
) -> str:
    lines = [
        "# Human-reviewed technical translation",
        "",
        f"- Source: {report.source.name}",
        f"- Review status: {_display_status(artifact.overall_status.value)}",
        f"- Reviewer: {artifact.reviewer_id}",
        f"- Reviewed: {artifact.created_at.isoformat()}",
        f"- Source SHA-256: `{artifact.source_sha256}`",
        f"- Base model report SHA-256: `{artifact.accepted_report_sha256}`",
        f"- Reviewed artifact SHA-256: `{reviewed_artifact_sha256}`",
        "",
    ]
    if artifact.decision_rationale:
        lines.extend(
            ["## Reviewer decision", "", artifact.decision_rationale, ""]
        )
    for number, section in enumerate(ALL_SECTIONS, start=1):
        lines.extend(_section_markdown(number, section, artifact, report))
    lines.extend(["## Change summary", ""])
    lines.extend(_change_summary(artifact.sections))
    lines.extend(["", "## Deterministic validation", ""])
    lines.extend(
        [
            f"- Valid contents: {artifact.validation.valid_content_count}",
            f"- Invalid contents: {artifact.validation.invalid_content_count}",
        ]
    )
    for content in artifact.validation.contents:
        for error in content.errors:
            lines.append(
                f"- `{content.owner}` / `{error.code.value}`: {error.message}"
            )
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in artifact.known_limitations)
    lines.append("")
    rendered = "\n".join(lines)
    return "\n".join(line.rstrip() for line in rendered.split("\n"))


@dataclass(frozen=True)
class _ReviewContext:
    report_path: Path
    run_dir: Path
    report: ReviewReport
    report_sha256: str
    report_relative_path: str
    reviewer_id: str
    sections: SectionReviewSet
    parent: SectionReviewParent | None


def _load_context(
    *,
    accepted_report_path: str | Path,
    reviewer_id: str,
    parent_review_path: str | Path | None,
) -> _ReviewContext:
    report_path = Path(accepted_report_path).resolve()
    report, report_sha256 = load_accepted_report(report_path)
    run_dir = report_path.parents[1]
    reviewer = reviewer_id.strip()
    if not reviewer:
        raise SectionReviewLifecycleError("reviewer_id is required")
    report_relative = report_path.relative_to(run_dir).as_posix()
    sections = SectionReviewSet()
    parent_reference = None
    if parent_review_path is not None:
        parent_path = Path(parent_review_path).resolve()
        try:
            relative_parent = parent_path.relative_to(run_dir)
        except ValueError as error:
            raise SectionReviewLifecycleError(
                "parent review must belong to the accepted run directory"
            ) from error
        parent = load_section_review(parent_path)
        if parent.action is not SectionReviewAction.SAVE_SECTION:
            raise SectionReviewLifecycleError(
                "finalized and rejected section reviews are terminal"
            )
        if parent.reviewer_id != reviewer:
            raise SectionReviewLifecycleError(
                "a section-review chain may contain only one reviewer identity"
            )
        if (
            parent.accepted_report_relative_path != report_relative
            or parent.accepted_report_sha256 != report_sha256
            or parent.source_sha256 != report.source.sha256
        ):
            raise SectionReviewLifecycleError(
                "parent review does not match the accepted report and source"
            )
        parent_bytes = parent_path.read_bytes()
        parent_reference = SectionReviewParent(
            relative_path=relative_parent.as_posix(),
            sha256=hashlib.sha256(parent_bytes).hexdigest(),
        )
        sections = parent.sections
    return _ReviewContext(
        report_path=report_path,
        run_dir=run_dir,
        report=report,
        report_sha256=report_sha256,
        report_relative_path=report_relative,
        reviewer_id=reviewer,
        sections=sections,
        parent=parent_reference,
    )


def _validate_reviews_against_report(
    sections: SectionReviewSet,
    report: ReviewReport,
) -> None:
    for section in NARRATIVE_SECTIONS:
        review = sections.get(section)
        if review is None:
            continue
        if not isinstance(review, NarrativeSectionReview):
            raise SectionReviewLifecycleError("narrative section has wrong review type")
        if review.model_content != getattr(report.translation, section.value):
            raise SectionReviewLifecycleError(
                f"{section.value} model content does not match accepted report"
            )
    for section in LIST_SECTIONS:
        review = sections.get(section)
        if review is None:
            continue
        if not isinstance(review, ListSectionReview):
            raise SectionReviewLifecycleError("list section has wrong review type")
        model_points = getattr(report.translation, section.value)
        references = [
            item for item in review.items if item.status is not ListItemReviewStatus.ADDED
        ]
        indices = [item.model_index for item in references]
        if indices != list(range(len(model_points))):
            raise SectionReviewLifecycleError(
                f"{section.value} must account for every model item once and in order"
            )
        for item in references:
            assert item.model_index is not None
            if item.model_item != model_points[item.model_index]:
                raise SectionReviewLifecycleError(
                    f"{section.value}[{item.model_index}] does not match accepted report"
                )


def _reviewed_list(review: ListSectionReview) -> tuple[TraceablePoint, ...]:
    return tuple(
        item.reviewed_item
        for item in review.items
        if item.reviewed_item is not None
    )


def _write_artifact(
    context: _ReviewContext,
    artifact: SectionReviewArtifact,
    *,
    final: bool,
) -> SavedSectionReview:
    artifact_json = (
        artifact.model_dump_json(
            indent=2,
            exclude_computed_fields=True,
        )
        + "\n"
    ).encode("utf-8")
    artifact_sha = hashlib.sha256(artifact_json).hexdigest()
    timestamp = artifact.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    qualifier = (
        artifact.updated_section.value
        if artifact.updated_section is not None
        else artifact.overall_status.value
    )
    review_dir = (
        context.run_dir
        / "section-reviews"
        / f"{timestamp}-{artifact.action.value}-{qualifier}-{artifact_sha[:10]}"
    )
    if review_dir.exists():
        raise SectionReviewOutputExistsError(
            f"section review directory already exists: {review_dir}"
        )
    json_path = review_dir / "review.json"
    markdown_path = review_dir / "review.md"
    write_bytes(json_path, artifact_json)
    markdown = render_combined_markdown(artifact, context.report, artifact_sha)
    write_bytes(markdown_path, markdown.encode("utf-8"))
    pdf_path = None
    manifest_path = None
    if final:
        from compoundx.review_pdf import write_review_pdf

        pdf_path = review_dir / "review.pdf"
        write_review_pdf(
            pdf_path,
            artifact=artifact,
            report=context.report,
            reviewed_artifact_sha256=artifact_sha,
        )
        manifest = FinalReviewManifest(
            schema_version="compoundx.section-review-manifest.v1",
            reviewed_artifact_sha256=artifact_sha,
            markdown_sha256=hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
            pdf_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            json_path=json_path.name,
            markdown_path=markdown_path.name,
            pdf_path=pdf_path.name,
        )
        manifest_path = review_dir / "manifest.json"
        write_model(manifest_path, manifest)
    return SavedSectionReview(
        artifact=artifact,
        review_dir=review_dir,
        json_path=json_path,
        markdown_path=markdown_path,
        pdf_path=pdf_path,
        manifest_path=manifest_path,
    )


def _section_markdown(
    number: int,
    section: DossierSection,
    artifact: SectionReviewArtifact,
    report: ReviewReport,
) -> list[str]:
    label = section.value.replace("_", " ").replace("tradeoffs", "trade-offs").title()
    lines = [f"## {number}. {label}", ""]
    review = artifact.sections.get(section)
    critic = next(
        (
            finding
            for finding in (report.critic.findings if report.critic else ())
            if finding.section is section
        ),
        None,
    )
    if section in NARRATIVE_SECTIONS:
        model_content = getattr(report.translation, section.value)
        lines.extend(
            ["### Model-generated translation", "", model_content.text, ""]
        )
        if model_content.assumptions:
            lines.extend(["**Model assumptions**", ""])
            lines.extend(f"- {item}" for item in model_content.assumptions)
            lines.append("")
        if model_content.uncertainties:
            lines.extend(["**Model uncertainties**", ""])
            lines.extend(f"- {item}" for item in model_content.uncertainties)
            lines.append("")
        if critic is not None:
            lines.extend(
                [
                    f"**Critic assessment:** {critic.assessment.value} - "
                    f"{critic.explanation}",
                    "**Semantic support:** "
                    + (
                        critic.support_assessment.value
                        if critic.support_assessment is not None
                        else "not recorded by this report version"
                    ),
                    "",
                ]
            )
        lines.extend(["**Model verified source evidence**", ""])
        lines.extend(
            _evidence_markdown(
                model_content,
                _validation_for_owner(
                    report.translation_validation,
                    section.value,
                ),
            )
        )
        if not isinstance(review, NarrativeSectionReview):
            lines.extend(["### Human review", "", "Not yet reviewed.", ""])
            return lines
        lines.extend(
            [
                "### Human review",
                "",
                f"**Section status:** {_display_status(review.status.value)}",
                "",
                "**Human-reviewed translation**",
                "",
                review.reviewed_content.text,
                "",
            ]
        )
        if review.rationale:
            lines.extend(
                ["**Reason for correction**", "", review.rationale, ""]
            )
        if review.reviewer_note:
            lines.extend(["**Reviewer note**", "", review.reviewer_note, ""])
        lines.extend(["### Verified source evidence", ""])
        validation = _validation_for_owner(artifact.validation, section.value)
        lines.extend(_evidence_markdown(review.reviewed_content, validation))
        return lines

    model_points = getattr(report.translation, section.value)
    if not isinstance(review, ListSectionReview):
        lines.extend(["### Model-generated items", ""])
        for index, point in enumerate(model_points, start=1):
            lines.extend([f"{index}. {point.text}", ""])
        lines.extend(["### Human review", "", "Not yet reviewed.", ""])
        return lines
    lines.extend(
        [
            "### Human review",
            "",
            f"**Section status:** {_display_status(review.status.value)}",
            "",
        ]
    )
    if review.reviewer_note:
        lines.extend(["**Reviewer note:** " + review.reviewer_note, ""])
    reviewed_index = 0
    for item_number, item in enumerate(review.items, start=1):
        lines.extend(
            [
                f"#### Item {item_number}: {_display_status(item.status.value)}",
                "",
            ]
        )
        if item.model_item is not None:
            lines.extend(
                ["**Model-generated item**", "", item.model_item.text, ""]
            )
            assert item.model_index is not None
            lines.extend(["**Model verified source evidence**", ""])
            lines.extend(
                _evidence_markdown(
                    item.model_item,
                    _validation_for_owner(
                        report.translation_validation,
                        f"{section.value}[{item.model_index + 1}]",
                    ),
                )
            )
        if item.reviewed_item is not None:
            reviewed_index += 1
            lines.extend(
                ["**Human-reviewed item**", "", item.reviewed_item.text, ""]
            )
            owner = f"{section.value}[{reviewed_index}]"
            validation = _validation_for_owner(artifact.validation, owner)
            lines.extend(["**Verified source evidence**", ""])
            lines.extend(_evidence_markdown(item.reviewed_item, validation))
        if item.rationale:
            lines.extend(["**Reason:** " + item.rationale, ""])
    return lines


def _change_summary(sections: SectionReviewSet) -> list[str]:
    lines: list[str] = []
    for section in ALL_SECTIONS:
        review = sections.get(section)
        label = section.value.replace("_", " ").replace("tradeoffs", "trade-offs")
        if review is None:
            lines.append(f"- {label}: not yet reviewed")
        elif isinstance(review, NarrativeSectionReview):
            lines.append(f"- {label}: {_display_status(review.status.value)}")
        else:
            counts = {
                status: sum(item.status is status for item in review.items)
                for status in ListItemReviewStatus
            }
            summary = ", ".join(
                f"{_display_status(status.value)}={count}"
                for status, count in counts.items()
                if count
            )
            lines.append(
                f"- {label}: {_display_status(review.status.value)} ({summary})"
            )
    return lines


def _evidence_markdown(content, validation) -> list[str]:
    lines: list[str] = []
    for quote in content.evidence_quotes:
        span = next(
            (span for span in validation.evidence_spans if span.quote == quote),
            None,
        )
        lines.extend([f"> {quote}", ""])
        lines.append(
            f"Page {span.page_number}"
            if span is not None and span.page_number is not None
            else "Page unavailable"
        )
        lines.append("")
    for citation in content.external_citations:
        lines.extend(
            [
                f"> External snapshot `{citation.source_id}`: {citation.quote}",
                "",
            ]
        )
    return lines


def _validation_for_owner(validation: DossierValidation, owner: str):
    return next(content for content in validation.contents if content.owner == owner)


def _source_document(report: ReviewReport) -> SourceDocument:
    return SourceDocument(
        source_name=report.source.name,
        sha256=report.source.sha256,
        text=report.source_text,
    )


def _display_status(value: str) -> str:
    return value.replace("_", " ").capitalize()


class SectionReviewLifecycleError(ValueError):
    """Raised when a section-review transition violates lifecycle rules."""


class SectionReviewOutputExistsError(FileExistsError):
    """Raised instead of overwriting a prior section-review artifact."""
