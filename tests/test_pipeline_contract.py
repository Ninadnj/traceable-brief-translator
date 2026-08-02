from __future__ import annotations

from typing import Any
from pathlib import Path

from compoundx.model_client import ModelCallResult
from compoundx.demo_app import load_latest_accepted_demo
from compoundx.pipeline import run_translation
from compoundx.schemas import (
    CriticAssessment,
    CriticOutput,
    DossierSection,
    ModelCallInfo,
    SectionFinding,
    SupportAssessment,
    TechnicalDossier,
    TraceablePoint,
    TranslationSection,
)


class ScriptedClient:
    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)

    def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_model: type,
    ) -> ModelCallResult:
        output = self.outputs.pop(0)
        assert isinstance(output, response_model)
        return ModelCallResult(
            output=output,
            info=ModelCallInfo(
                model="scripted-model",
                response_model=response_model.__name__,
                duration_seconds=0.01,
            ),
        )


def test_pipeline_renders_one_connected_dossier(tmp_path) -> None:
    need = "The enclosure must protect the instrument during transport."
    gap = "No transport test or allowable damage limit has been defined."
    source_path = tmp_path / "brief.md"
    source_path.write_text(f"{need}\n{gap}\n", encoding="utf-8")
    dossier = TechnicalDossier(
        product_intent=TranslationSection(
            text="Keep the instrument protected during transport.",
            evidence_quotes=(need,),
        ),
        component_function=TranslationSection(
            text="The enclosure must shield and retain the instrument.",
            evidence_quotes=(need,),
            assumptions=("Protection requires shielding and retention.",),
        ),
        performance_to_validate=TranslationSection(
            text=(
                "The complete enclosure should maintain protection under "
                "representative transport exposure."
            ),
            evidence_quotes=(need, gap),
            uncertainties=("The test and allowable damage remain undefined.",),
        ),
        material_relevant_criteria=TranslationSection(
            text="Resistance to transport damage may influence performance.",
            evidence_quotes=(need,),
            uncertainties=("No material-property target is supported.",),
        ),
        missing_information=(
            TraceablePoint(
                text="Define the transport test and allowable damage.",
                evidence_quotes=(gap,),
            ),
        ),
        conflicts_and_tradeoffs=(
            TraceablePoint(
                text=(
                    "Protection expectations cannot yet be balanced against an "
                    "undefined allowable-damage limit."
                ),
                evidence_quotes=(need, gap),
            ),
        ),
    )
    critic = CriticOutput(
        contract_version="compoundx.critic.v2",
        findings=tuple(
            SectionFinding(
                section=section,
                assessment=CriticAssessment.SUPPORTED,
                support_assessment=SupportAssessment.DIRECT,
                explanation="The section remains traceable and qualified.",
                human_review_required=False,
            )
            for section in DossierSection
        )
    )
    invalid_dossier = dossier.model_copy(
        update={
            "product_intent": dossier.product_intent.model_copy(
                update={"text": "Keep one instrument protected during transport."}
            )
        }
    )

    result = run_translation(
        source_path=source_path,
        output_dir=tmp_path / "run",
        client=ScriptedClient(invalid_dossier, dossier, critic),
    )

    assert not result.has_mechanical_errors
    assert result.report.initial_translation == invalid_dossier
    assert result.report.initial_translation_validation is not None
    assert result.report.initial_translation_validation.invalid_content_count == 1
    assert result.report.initial_translator_call is not None
    assert result.report.translator_repair_call is not None
    assert result.report.translation == dossier
    rendered = result.markdown_path.read_text(encoding="utf-8")
    assert "## 1. Product intent" in rendered
    assert "## 4. Material-relevant criteria" in rendered
    assert "## 5. Missing information" in rendered
    assert "## 6. Conflicts and trade-offs" in rendered
    assert "- Dossier sections: 6" in rendered
    assert "- Translation levels: 4" in rendered
    assert "Parent:" not in rendered
    assert "Model-generated ID" not in rendered
    assert "Bounded translator repair: completed and fully recorded" in rendered

    accepted_run = tmp_path / "saved-demo"
    source_copy = accepted_run / "source" / source_path.name
    acceptance = accepted_run / "acceptance"
    source_copy.parent.mkdir(parents=True)
    acceptance.mkdir()
    source_copy.write_bytes(source_path.read_bytes())
    (acceptance / "report.json").write_text(
        result.report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (acceptance / "report.md").write_text(rendered, encoding="utf-8")

    reloaded = load_latest_accepted_demo(tmp_path)

    assert reloaded is not None
    assert reloaded.run_dir == accepted_run
    assert reloaded.completed.report.source.sha256 == result.report.source.sha256
