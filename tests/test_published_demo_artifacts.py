from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from compoundx.section_review import (
    ALL_SECTIONS,
    FinalReviewManifest,
    OverallReviewStatus,
    SectionReviewAction,
    load_accepted_report,
    load_section_review,
)
from compoundx.semantic_evaluation import (
    load_semantic_evaluation,
    render_semantic_evaluation,
)


DEMO_RESULTS = Path(__file__).parents[1] / "demo-results"
REVIEWER_ID = "Nina Doinjashvili"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_evidence_path(document: object, path: str) -> object:
    current = document
    for part in path.split("."):
        match = re.fullmatch(r"([a-z_]+)(?:\[([0-9]+)\])?", part)
        assert match is not None, f"not an artifact path: {path}"
        assert isinstance(current, dict), f"non-object in artifact path: {path}"
        name, raw_index = match.groups()
        assert name in current, f"missing field in artifact path: {path}"
        current = current[name]
        if raw_index is not None:
            assert isinstance(current, list), f"non-list in artifact path: {path}"
            current = current[int(raw_index)]
    return current


@pytest.mark.parametrize("case_slug", ("roller-skate", "garden-trimmer"))
def test_published_demo_has_complete_hash_linked_review_and_evaluation(
    case_slug: str,
) -> None:
    run_dir = DEMO_RESULTS / case_slug
    accepted_report_path = run_dir / "acceptance" / "report.json"
    report, accepted_report_sha256 = load_accepted_report(accepted_report_path)

    source_path = run_dir / "source" / report.source.name
    assert source_path.is_file()
    assert _sha256(source_path) == report.source.sha256

    review_paths = tuple(sorted(run_dir.glob("section-reviews/*/review.json")))
    assert len(review_paths) == len(ALL_SECTIONS) + 1
    chain = tuple(
        sorted(
            ((path, load_section_review(path)) for path in review_paths),
            key=lambda item: item[1].created_at,
        )
    )
    save_chain = chain[:-1]
    final_path, final = chain[-1]

    assert [artifact.action for _, artifact in save_chain] == [
        SectionReviewAction.SAVE_SECTION
    ] * len(ALL_SECTIONS)
    assert [artifact.updated_section for _, artifact in save_chain] == list(
        ALL_SECTIONS
    )
    assert final.action is SectionReviewAction.FINALIZE
    assert final.updated_section is None
    assert all(
        earlier.created_at < later.created_at
        for (_, earlier), (_, later) in zip(chain, chain[1:])
    )

    expected_report_relative_path = accepted_report_path.relative_to(
        run_dir
    ).as_posix()
    for index, (path, artifact) in enumerate(chain):
        assert path.with_suffix(".md").is_file()
        assert artifact.reviewer_id == REVIEWER_ID
        assert artifact.accepted_report_relative_path == (
            expected_report_relative_path
        )
        assert artifact.accepted_report_sha256 == accepted_report_sha256
        assert artifact.source_sha256 == report.source.sha256

        if index == 0:
            assert artifact.parent_review is None
        else:
            previous_path, _ = chain[index - 1]
            assert artifact.parent_review is not None
            assert artifact.parent_review.relative_path == previous_path.relative_to(
                run_dir
            ).as_posix()
            parent_path = (run_dir / artifact.parent_review.relative_path).resolve()
            assert parent_path == previous_path.resolve()
            assert artifact.parent_review.sha256 == _sha256(previous_path)

    for index, (_, artifact) in enumerate(save_chain, start=1):
        populated_sections = tuple(
            section
            for section in ALL_SECTIONS
            if artifact.sections.get(section) is not None
        )
        assert populated_sections == ALL_SECTIONS[:index]

    assert final.sections.complete
    assert final.overall_status is OverallReviewStatus.APPROVED_WITH_CORRECTIONS
    assert final.validation.invalid_content_count == 0
    assert all(content.valid for content in final.validation.contents)

    manifest_path = final_path.parent / "manifest.json"
    markdown_path = final_path.parent / "review.md"
    pdf_path = final_path.parent / "review.pdf"
    manifest = FinalReviewManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )

    assert manifest.json_path == final_path.name == "review.json"
    assert manifest.markdown_path == markdown_path.name == "review.md"
    assert manifest.pdf_path == pdf_path.name == "review.pdf"
    assert manifest.reviewed_artifact_sha256 == _sha256(final_path)
    assert manifest.markdown_sha256 == _sha256(markdown_path)
    assert manifest.pdf_sha256 == _sha256(pdf_path)
    assert all(
        line == line.rstrip()
        for line in markdown_path.read_text(encoding="utf-8").splitlines()
    )
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(pdf_path).pages
    )
    compact_pdf_text = " ".join(pdf_text.split())
    assert "Human-reviewed technical translation" in compact_pdf_text
    assert REVIEWER_ID in compact_pdf_text

    evaluation_path = run_dir / "evaluation" / "evaluation.json"
    evaluation = load_semantic_evaluation(evaluation_path)
    assert evaluation.evaluation_method == "model_assisted"
    assert not Path(evaluation.report_path).is_absolute()

    evaluated_report_path = (run_dir / evaluation.report_path).resolve()
    evaluated_report_path.relative_to(run_dir.resolve())
    assert evaluated_report_path == final_path.resolve()
    assert evaluation.report_path == final_path.relative_to(run_dir).as_posix()
    assert evaluation.report_sha256 == _sha256(final_path)
    assert evaluation.source_sha256 == report.source.sha256
    assert evaluation.evaluated_at >= final.created_at
    assert evaluation.overall_pass
    final_document = json.loads(final_path.read_text(encoding="utf-8"))
    evidence_paths = (
        evidence
        for item in (*evaluation.scores, *evaluation.adversarial_checks)
        for evidence in item.report_evidence
    )
    for evidence_path in evidence_paths:
        _resolve_evidence_path(final_document, evidence_path)

    evaluation_markdown_path = evaluation_path.with_suffix(".md")
    assert evaluation_markdown_path.read_text(
        encoding="utf-8"
    ) == render_semantic_evaluation(evaluation)
