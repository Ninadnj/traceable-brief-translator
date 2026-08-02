from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from compoundx import demo_app
from compoundx.section_review import SectionReviewAction
from streamlit.testing.v1 import AppTest


class Upload:
    name = "../target.pdf"

    @staticmethod
    def getvalue() -> bytes:
        return b"demo pdf bytes"


def test_demo_landing_page_requests_a_session_only_key() -> None:
    app = AppTest.from_file("streamlit_app.py").run(timeout=30)

    assert not app.exception
    assert app.title[0].value == "Traceable technical translation"
    assert [tab.label for tab in app.tabs] == [
        "1. Translate",
        "2. Review",
        "3. Final report",
        "4. Verification",
    ]
    assert [field.label for field in app.text_input][:3] == [
        "OpenAI API key - this browser session only",
        "Translator model",
        "Optional distinct critic model",
    ]
    # The published terminal chain is immutable: the app must not reconstruct
    # the latest save-section snapshot as an editable active review.
    buttons = [button.label for button in app.button]
    assert "Save Product intent review" not in buttons
    assert "Save Missing information review" not in buttons
    assert "Finalize reviewed report" not in buttons
    assert "Reject whole dossier" not in buttons
    downloads = [item.label for item in app.get("download_button")]
    assert "Download reviewed PDF" in downloads
    assert "Download reviewed JSON" in downloads
    assert "Download original model report" in downloads
    assert any(
        item.value == "### Model-assisted supporting evidence"
        for item in app.markdown
    )


def test_final_pdf_uses_streamlits_native_viewer() -> None:
    source = Path("src/compoundx/demo_app.py").read_text(encoding="utf-8")

    assert "st.pdf(" in source
    assert "data:application/pdf" not in source


def test_run_saved_demo_keeps_source_and_reports_without_saving_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client_arguments: list[dict[str, str]] = []

    class FakeClient:
        def __init__(self, **arguments: str) -> None:
            client_arguments.append(arguments)

    def fake_run_translation(**arguments: object) -> SimpleNamespace:
        output_dir = Path(arguments["output_dir"])
        output_dir.mkdir()
        json_path = output_dir / "report.json"
        markdown_path = output_dir / "report.md"
        json_path.write_bytes(b'{"translation": "saved"}\n')
        markdown_path.write_bytes(b"# Saved translation\n")
        return SimpleNamespace(
            report=object(),
            json_path=json_path,
            markdown_path=markdown_path,
        )

    monkeypatch.setattr(demo_app, "OpenAIModelClient", FakeClient)
    monkeypatch.setattr(demo_app, "run_translation", fake_run_translation)

    saved = demo_app.run_saved_demo(
        brief_upload=Upload(),
        api_key="secret-live-key",
        model="translator-model",
        critic_model="",
        results_root=tmp_path,
    )

    assert saved.source_path.name == "target.pdf"
    assert saved.source_path.read_bytes() == b"demo pdf bytes"
    assert saved.completed.report_json == b'{"translation": "saved"}\n'
    assert saved.completed.report_markdown == b"# Saved translation\n"
    assert client_arguments == [
        {"model": "translator-model", "api_key": "secret-live-key"}
    ]
    assert "secret-live-key" not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in saved.run_dir.rglob("*")
        if path.is_file()
    )


def test_saved_source_directory_is_immutable_and_content_addressed(
    tmp_path: Path,
) -> None:
    saved = demo_app.save_demo_source(
        uploaded_name="../../Roller Skate.pdf",
        content=b"brief",
        results_root=tmp_path,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert saved.source_path == saved.run_dir / "source" / "Roller Skate.pdf"
    assert saved.source_path.read_bytes() == b"brief"
    assert saved.run_dir.name.startswith("Roller-Skate-20260801T000000000000Z-")


def test_terminal_review_wins_over_a_later_save_section() -> None:
    accepted_sha = "a" * 64
    source_sha = "b" * 64
    terminal = SimpleNamespace(
        action=SectionReviewAction.FINALIZE,
        accepted_report_sha256=accepted_sha,
        source_sha256=source_sha,
        created_at=datetime(2026, 8, 3, 10, tzinfo=UTC),
    )
    later_save = SimpleNamespace(
        action=SectionReviewAction.SAVE_SECTION,
        accepted_report_sha256=accepted_sha,
        source_sha256=source_sha,
        created_at=datetime(2026, 8, 3, 11, tzinfo=UTC),
    )

    selected = demo_app._select_terminal_review(
        [
            (Path("section-reviews/final/review.json"), terminal),
            (Path("section-reviews/later-save/review.json"), later_save),
        ],
        accepted_report_sha256=accepted_sha,
        source_sha256=source_sha,
    )

    assert selected is not None
    assert selected[1] is terminal


def test_terminal_chain_validation_rejects_a_tampered_parent(tmp_path: Path) -> None:
    source_run = demo_app.DEMO_RESULTS_ROOT / "roller-skate"
    run_dir = tmp_path / "roller-skate"
    shutil.copytree(source_run / "acceptance", run_dir / "acceptance")
    shutil.copytree(source_run / "section-reviews", run_dir / "section-reviews")
    accepted_path = run_dir / "acceptance" / "report.json"
    report, accepted_sha = demo_app.load_accepted_report(accepted_path)
    records = [
        (path, demo_app.load_section_review(path))
        for path in demo_app.list_section_reviews(run_dir)
    ]
    terminal = demo_app._select_terminal_review(
        records,
        accepted_report_sha256=accepted_sha,
        source_sha256=report.source.sha256,
    )
    assert terminal is not None
    terminal_path, terminal_artifact = terminal
    assert terminal_artifact.parent_review is not None
    parent_path = run_dir / terminal_artifact.parent_review.relative_path
    parent_path.write_bytes(parent_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="parent hash mismatch"):
        demo_app._validate_terminal_review_chain(
            run_dir=run_dir,
            accepted_report_path=accepted_path,
            accepted_report_sha256=accepted_sha,
            source_sha256=report.source.sha256,
            terminal_path=terminal_path,
            terminal_artifact=terminal_artifact,
        )


def test_final_output_validation_rejects_a_tampered_pdf(tmp_path: Path) -> None:
    source_reviews = (
        demo_app.DEMO_RESULTS_ROOT / "roller-skate" / "section-reviews"
    )
    source_final = next(source_reviews.glob("*-finalize-*"))
    final_dir = tmp_path / source_final.name
    shutil.copytree(source_final, final_dir)
    review_path = final_dir / "review.json"

    _, pdf_path = demo_app._validate_final_review_outputs(review_path)
    pdf_path.write_bytes(pdf_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="hash mismatch: review.pdf"):
        demo_app._validate_final_review_outputs(review_path)


def _write_supporting_evaluation(
    run_dir: Path,
    final_review_path: Path,
    *,
    source_sha256: str,
    updates: dict[str, object] | None = None,
) -> None:
    criteria = (
        "product_intent_preserves_priorities",
        "component_function_is_clear",
        "performance_is_observable",
        "material_criteria_are_cautious",
        "missing_information_is_complete",
        "tradeoffs_are_represented",
        "evidence_is_traceable",
        "output_is_concise_and_readable",
    )
    payload: dict[str, object] = {
        "schema_version": "compoundx.semantic-evaluation.v2",
        "case_name": "Published case",
        "source_sha256": source_sha256,
        "report_sha256": hashlib.sha256(final_review_path.read_bytes()).hexdigest(),
        "report_path": final_review_path.relative_to(run_dir).as_posix(),
        "evaluated_at": "2026-08-03T12:00:00Z",
        "evaluation_method": "model_assisted",
        "evaluator": "Codex supporting rubric",
        "scores": [
            {
                "criterion": criterion,
                "score": 4,
                "rationale": "The final reviewed content meets this criterion.",
                "report_evidence": ["reviewed_dossier"],
            }
            for criterion in criteria
        ],
        "adversarial_checks": [
            {
                "check_id": "qualifier_force",
                "expectation": "Preserve qualified language.",
                "observed_treatment": "Qualified language is preserved.",
                "report_evidence": ["reviewed_dossier"],
                "passed": True,
            }
        ],
        "summary": "Supporting rubric only; it does not determine eligibility.",
    }
    payload.update(updates or {})
    evaluation_dir = run_dir / "evaluation"
    evaluation_dir.mkdir()
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_supporting_evaluation_must_link_exact_final_review(tmp_path: Path) -> None:
    run_dir = tmp_path / "case"
    final_review_path = run_dir / "section-reviews" / "final" / "review.json"
    final_review_path.parent.mkdir(parents=True)
    final_review_path.write_bytes(b'{"action":"finalize"}\n')
    source_sha = "c" * 64
    _write_supporting_evaluation(
        run_dir,
        final_review_path,
        source_sha256=source_sha,
    )

    evaluation = demo_app._load_linked_supporting_evaluation(
        run_dir=run_dir,
        final_review_path=final_review_path,
        source_sha256=source_sha,
    )

    assert evaluation is not None
    assert evaluation.evaluation_method == "model_assisted"
    assert evaluation.report_path == "section-reviews/final/review.json"


@pytest.mark.parametrize(
    "updates",
    (
        {"report_path": "section-reviews/another-final/review.json"},
        {"report_path": "../outside/review.json"},
        {"report_sha256": "d" * 64},
        {"source_sha256": "e" * 64},
        {"evaluation_method": "human"},
    ),
)
def test_supporting_evaluation_rejects_wrong_target(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    run_dir = tmp_path / "case"
    final_review_path = run_dir / "section-reviews" / "final" / "review.json"
    final_review_path.parent.mkdir(parents=True)
    final_review_path.write_bytes(b'{"action":"finalize"}\n')
    source_sha = "f" * 64
    _write_supporting_evaluation(
        run_dir,
        final_review_path,
        source_sha256=source_sha,
        updates=updates,
    )

    with pytest.raises(ValueError):
        demo_app._load_linked_supporting_evaluation(
            run_dir=run_dir,
            final_review_path=final_review_path,
            source_sha256=source_sha,
        )
