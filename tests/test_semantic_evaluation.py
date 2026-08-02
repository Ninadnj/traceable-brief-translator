from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from compoundx.semantic_evaluation import (
    AdversarialCheck,
    CriterionScore,
    RubricCriterion,
    SemanticEvaluation,
    SemanticEvaluationOutputExistsError,
    load_semantic_evaluation,
    render_semantic_evaluation,
    write_semantic_evaluation,
)


def _scores(score: int = 5) -> tuple[CriterionScore, ...]:
    return tuple(
        CriterionScore(
            criterion=criterion,
            score=score,
            rationale="The saved report satisfies this criterion.",
            report_evidence=("Relevant dossier section",),
        )
        for criterion in RubricCriterion
    )


def _evaluation(**updates: object) -> SemanticEvaluation:
    values = {
        "schema_version": "compoundx.semantic-evaluation.v2",
        "case_name": "Generic altered case",
        "source_sha256": "a" * 64,
        "report_sha256": "b" * 64,
        "report_path": "acceptance/report.json",
        "evaluated_at": datetime(2026, 8, 1, tzinfo=UTC),
        "evaluation_method": "model_assisted",
        "evaluator": "Codex",
        "scores": _scores(),
        "adversarial_checks": (
            AdversarialCheck(
                check_id="unsupported_claim",
                expectation="The weak claim remains qualified.",
                observed_treatment="The dossier labels it unsupported.",
                report_evidence=("Conflicts and trade-offs",),
                passed=True,
            ),
        ),
        "summary": "The case passes the model-assisted semantic review.",
    }
    values.update(updates)
    return SemanticEvaluation.model_validate(values)


def test_semantic_evaluation_requires_each_criterion_once() -> None:
    duplicate_scores = (*_scores()[:-1], _scores()[0])

    with pytest.raises(ValidationError, match="every rubric criterion once"):
        _evaluation(scores=duplicate_scores)


def test_overall_pass_is_derived_from_scores_and_adversarial_checks() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _evaluation(overall_pass=True)

    failed = _evaluation(scores=_scores(score=3))
    assert not failed.overall_pass
    assert failed.mean_score == 3.0


def test_semantic_evaluation_renders_and_never_overwrites(tmp_path) -> None:
    evaluation = _evaluation()
    rendered = render_semantic_evaluation(evaluation)

    assert "Mean score: 5.00/5" in rendered
    assert "unsupported_claim: PASS" in rendered

    json_path, markdown_path = write_semantic_evaluation(
        tmp_path / "evaluation",
        evaluation,
    )

    assert '"overall_pass"' not in json_path.read_text(encoding="utf-8")
    assert load_semantic_evaluation(json_path) == evaluation
    assert markdown_path.read_text(encoding="utf-8") == rendered
    with pytest.raises(SemanticEvaluationOutputExistsError):
        write_semantic_evaluation(tmp_path / "evaluation", evaluation)
