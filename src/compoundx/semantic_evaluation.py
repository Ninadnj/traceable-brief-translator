"""Small, human-authored semantic rubric for saved demonstrations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator

from compoundx.artifact_io import write_bytes
from compoundx.models import CompoundXModel


class RubricCriterion(str, Enum):
    PRODUCT_INTENT = "product_intent_preserves_priorities"
    COMPONENT_FUNCTION = "component_function_is_clear"
    PERFORMANCE = "performance_is_observable"
    MATERIAL_CRITERIA = "material_criteria_are_cautious"
    MISSING_INFORMATION = "missing_information_is_complete"
    TRADE_OFFS = "tradeoffs_are_represented"
    TRACEABILITY = "evidence_is_traceable"
    READABILITY = "output_is_concise_and_readable"


class CriterionScore(CompoundXModel):
    criterion: RubricCriterion
    score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1)
    report_evidence: tuple[str, ...] = Field(min_length=1)


class AdversarialCheck(CompoundXModel):
    check_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    expectation: str = Field(min_length=1)
    observed_treatment: str = Field(min_length=1)
    report_evidence: tuple[str, ...] = Field(min_length=1)
    passed: bool


class SemanticEvaluation(CompoundXModel):
    schema_version: Literal["compoundx.semantic-evaluation.v2"]
    case_name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_path: str = Field(min_length=1)
    evaluated_at: datetime
    evaluation_method: Literal["model_assisted", "human"]
    evaluator: str = Field(min_length=1)
    scores: tuple[CriterionScore, ...] = Field(min_length=8, max_length=8)
    adversarial_checks: tuple[AdversarialCheck, ...] = Field(min_length=1)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def complete(self) -> SemanticEvaluation:
        _require_complete_scores(self.scores)
        return self

    @computed_field(return_type=bool)
    @property
    def overall_pass(self) -> bool:
        return _calculate_pass(self.scores, self.adversarial_checks)

    @property
    def mean_score(self) -> float:
        return sum(score.score for score in self.scores) / len(self.scores)


def _require_complete_scores(scores: tuple[CriterionScore, ...]) -> None:
    counts = {
        criterion: sum(score.criterion is criterion for score in scores)
        for criterion in RubricCriterion
    }
    invalid = {
        criterion.value: count
        for criterion, count in counts.items()
        if count != 1
    }
    if invalid:
        raise ValueError(
            "semantic evaluation must score every rubric criterion once: "
            f"{invalid}"
        )


def _calculate_pass(
    scores: tuple[CriterionScore, ...],
    checks: tuple[AdversarialCheck, ...],
) -> bool:
    return all(score.score >= 4 for score in scores) and all(
        check.passed for check in checks
    )


def render_semantic_evaluation(evaluation: SemanticEvaluation) -> str:
    title = (
        "Model-assisted semantic evaluation"
        if evaluation.evaluation_method == "model_assisted"
        else "Human semantic evaluation"
    )
    lines = [
        f"# {title}",
        "",
        f"- Case: {evaluation.case_name}",
        f"- Evaluation method: {evaluation.evaluation_method}",
        f"- Evaluator: {evaluation.evaluator}",
        f"- Evaluated: {evaluation.evaluated_at.isoformat()}",
        f"- Evaluated artifact: `{evaluation.report_path}`",
        f"- Source SHA-256: `{evaluation.source_sha256}`",
        f"- Report SHA-256: `{evaluation.report_sha256}`",
        f"- Mean score: {evaluation.mean_score:.2f}/5",
        f"- Overall result: {'PASS' if evaluation.overall_pass else 'FAIL'}",
        "",
        "## Rubric scores",
        "",
        "| Criterion | Score | Rationale |",
        "| --- | ---: | --- |",
    ]
    for score in evaluation.scores:
        evidence = "; ".join(score.report_evidence)
        lines.append(
            f"| {score.criterion.value} | {score.score}/5 | "
            f"{score.rationale} Evidence: {evidence} |"
        )
    lines.extend(["", "## Adversarial checks", ""])
    for check in evaluation.adversarial_checks:
        status = "PASS" if check.passed else "FAIL"
        lines.extend(
            [
                f"### {check.check_id}: {status}",
                "",
                f"**Expectation:** {check.expectation}",
                "",
                f"**Observed treatment:** {check.observed_treatment}",
                "",
                "**Report evidence:**",
                "",
                *(f"- {item}" for item in check.report_evidence),
                "",
            ]
        )
    lines.extend(["## Evaluation summary", "", evaluation.summary, ""])
    return "\n".join(lines)


def write_semantic_evaluation(
    output_dir: str | Path,
    evaluation: SemanticEvaluation,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    if output.exists():
        raise SemanticEvaluationOutputExistsError(
            f"semantic evaluation directory already exists: {output}"
        )
    json_path = output / "evaluation.json"
    markdown_path = output / "evaluation.md"
    write_bytes(
        json_path,
        (
            evaluation.model_dump_json(
                indent=2,
                exclude_computed_fields=True,
            )
            + "\n"
        ).encode("utf-8"),
    )
    write_bytes(
        markdown_path,
        render_semantic_evaluation(evaluation).encode("utf-8"),
    )
    return json_path, markdown_path


def load_semantic_evaluation(
    path: str | Path,
) -> SemanticEvaluation:
    """Strictly load the current evaluation artifact."""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    version = value.get("schema_version") if isinstance(value, dict) else None
    if version == "compoundx.semantic-evaluation.v2":
        return SemanticEvaluation.model_validate(value)
    raise ValueError(f"unsupported semantic evaluation schema: {version!r}")


class SemanticEvaluationOutputExistsError(FileExistsError):
    """Raised instead of overwriting a saved semantic evaluation."""
