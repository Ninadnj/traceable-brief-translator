"""External knowledge stays separable from the brief.

The guarantee under test is directional: a quote is verified against the channel
it was cited in, never against the other one. Brief evidence and frozen-snapshot
evidence therefore cannot silently substitute for each other.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from compoundx.external_sources import (
    ExternalSourcePackError,
    ExternalSourcePackInput,
    load_external_sources,
)
from compoundx.model_client import ModelCallResult
from compoundx.models import SourceDocument
from compoundx.pipeline import run_translation
from compoundx.schemas import (
    CriticAssessment,
    CriticOutput,
    DossierSection,
    ExternalCitation,
    ExternalSource,
    ModelCallInfo,
    SectionFinding,
    SupportAssessment,
    TechnicalDossier,
    TraceablePoint,
    TranslationSection,
    ValidationErrorCode,
)
from compoundx.verifier import verify_dossier


BRIEF_NEED = "The moulded housing must survive handling in cold storage."
BRIEF_GAP = "No cold-temperature test protocol has been agreed."
BRIEF_PREFERENCE = "Mass reduction is desirable if durability is unaffected."
BRIEF_FEEDSTOCK = "Recycled feedstock is expected in the finished part."
BRIEF_TEXT = f"{BRIEF_NEED}\n{BRIEF_GAP}\n{BRIEF_PREFERENCE}\n{BRIEF_FEEDSTOCK}\n"

SNAPSHOT_STIFFENS = "Chain mobility falls as temperature drops and the material stiffens."
SNAPSHOT_TRANSITION = (
    "The transition shifts with strain rate, notch sharpness and wall thickness."
)
SNAPSHOT_RATES = "Cold behaviour is confirmed on the component at 2 loading rates."
SNAPSHOT_REGRIND = "Regrind batches vary, so a plaque value is an upper reference."
SNAPSHOT_TEXT = (
    f"{SNAPSHOT_STIFFENS}\n{SNAPSHOT_TRANSITION}\n"
    f"{SNAPSHOT_RATES}\n{SNAPSHOT_REGRIND}\n"
)

EXAMPLE_PACK_DIR = Path(__file__).parents[1] / "examples" / "external-sources"


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


def _source(text: str = BRIEF_TEXT) -> SourceDocument:
    return SourceDocument(
        source_name="brief.md",
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
    )


def _external(
    text: str = SNAPSHOT_TEXT,
    *,
    source_id: str = "notes",
) -> tuple[ExternalSource, ...]:
    return (
        ExternalSource(
            source_id=source_id,
            title="Illustrative placeholder snapshot",
            locator="example://frozen-snapshot/test-notes",
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
        ),
    )


def _dossier() -> TechnicalDossier:
    """A number-free baseline so each test isolates one citation behaviour."""

    return TechnicalDossier(
        product_intent=TranslationSection(
            text="Keep the housing serviceable through cold handling.",
            evidence_quotes=(BRIEF_NEED,),
        ),
        component_function=TranslationSection(
            text="The housing retains and protects what it encloses.",
            evidence_quotes=(BRIEF_NEED,),
        ),
        performance_to_validate=TranslationSection(
            text="Cold handling performance remains to be defined.",
            evidence_quotes=(BRIEF_GAP,),
            uncertainties=("No protocol or acceptance criterion exists yet.",),
        ),
        material_relevant_criteria=TranslationSection(
            text="Cold fracture behaviour and feedstock consistency may matter.",
            evidence_quotes=(BRIEF_FEEDSTOCK,),
        ),
        missing_information=(
            TraceablePoint(
                text="Define the cold-temperature test protocol.",
                evidence_quotes=(BRIEF_GAP,),
            ),
        ),
        conflicts_and_tradeoffs=(
            TraceablePoint(
                text="Mass reduction must be balanced against durability.",
                evidence_quotes=(BRIEF_PREFERENCE,),
            ),
        ),
    )


def _with_intent(section: TranslationSection) -> TechnicalDossier:
    return _dossier().model_copy(update={"product_intent": section})


def _critic() -> CriticOutput:
    return CriticOutput(
        contract_version="compoundx.critic.v2",
        findings=tuple(
            SectionFinding(
                section=section,
                assessment=CriticAssessment.SUPPORTED,
                support_assessment=SupportAssessment.DIRECT,
                explanation="The section is traceable and appropriately qualified.",
                human_review_required=False,
            )
            for section in DossierSection
        )
    )


def _codes(validation: Any) -> set[ValidationErrorCode]:
    return {error.code for error in validation.errors}


def test_brief_quote_cited_as_external_does_not_verify() -> None:
    """Centrepiece: the brief cannot satisfy an external citation."""

    dossier = _with_intent(
        TranslationSection(
            text="Introduced context about cold handling.",
            external_citations=(
                ExternalCitation(source_id="notes", quote=BRIEF_NEED),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert not validation.valid
    assert _codes(validation) == {ValidationErrorCode.MISSING_EXTERNAL_QUOTE}
    assert validation.external_evidence_spans == ()
    assert validation.errors[0].value == BRIEF_NEED

    control = _with_intent(
        TranslationSection(
            text="The identical words, cited against the brief instead.",
            evidence_quotes=(BRIEF_NEED,),
        )
    )
    assert verify_dossier(_source(), control, _external()).contents[0].valid


def test_snapshot_quote_cited_as_brief_evidence_does_not_verify() -> None:
    """Centrepiece: a frozen snapshot cannot satisfy a brief citation."""

    dossier = _with_intent(
        TranslationSection(
            text="A claim presented as if the brief had supplied it.",
            evidence_quotes=(SNAPSHOT_TRANSITION,),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert not validation.valid
    assert _codes(validation) == {ValidationErrorCode.MISSING_QUOTE}
    assert validation.evidence_spans == ()
    assert validation.errors[0].value == SNAPSHOT_TRANSITION

    control = _with_intent(
        TranslationSection(
            text="The identical words, cited against the snapshot instead.",
            external_citations=(
                ExternalCitation(source_id="notes", quote=SNAPSHOT_TRANSITION),
            ),
        )
    )
    assert verify_dossier(_source(), control, _external()).contents[0].valid


def test_valid_external_citation_is_reported_in_its_own_channel() -> None:
    dossier = _with_intent(
        TranslationSection(
            text="Cold behaviour depends on the brief need and on general context.",
            evidence_quotes=(BRIEF_NEED,),
            external_citations=(
                ExternalCitation(source_id="notes", quote=SNAPSHOT_TRANSITION),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert validation.valid
    assert not validation.errors
    assert [span.quote for span in validation.evidence_spans] == [BRIEF_NEED]
    assert [span.quote for span in validation.external_evidence_spans] == [
        SNAPSHOT_TRANSITION
    ]
    external_span = validation.external_evidence_spans[0]
    assert external_span.source_id == "notes"
    assert SNAPSHOT_TEXT[external_span.start:external_span.end] == (
        external_span.source_quote
    )
    assert BRIEF_TEXT[
        validation.evidence_spans[0].start:validation.evidence_spans[0].end
    ] == validation.evidence_spans[0].source_quote


def test_external_quote_absent_from_the_frozen_snapshot_is_rejected() -> None:
    dossier = _with_intent(
        TranslationSection(
            text="An introduced claim nobody can locate.",
            external_citations=(
                ExternalCitation(
                    source_id="notes",
                    quote="Cold behaviour is irrelevant below the glass transition.",
                ),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert not validation.valid
    assert _codes(validation) == {ValidationErrorCode.MISSING_EXTERNAL_QUOTE}
    assert validation.external_evidence_spans == ()


def test_unknown_external_source_id_is_rejected() -> None:
    dossier = _with_intent(
        TranslationSection(
            text="An introduced claim attributed to a source nobody supplied.",
            external_citations=(
                ExternalCitation(
                    source_id="handbook_that_was_never_supplied",
                    quote=SNAPSHOT_TRANSITION,
                ),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert not validation.valid
    assert _codes(validation) == {ValidationErrorCode.UNKNOWN_EXTERNAL_SOURCE}
    assert validation.errors[0].value == "handbook_that_was_never_supplied"
    assert validation.external_evidence_spans == ()


def test_repeated_snapshot_quote_is_ambiguous() -> None:
    repeated = "Notch sharpness matters."
    dossier = _with_intent(
        TranslationSection(
            text="An introduced claim with an unlocatable position.",
            external_citations=(
                ExternalCitation(source_id="notes", quote=repeated),
            ),
        )
    )

    validation = verify_dossier(
        _source(),
        dossier,
        _external(f"{repeated} {repeated}\n"),
    ).contents[0]

    assert not validation.valid
    assert _codes(validation) == {ValidationErrorCode.AMBIGUOUS_EXTERNAL_QUOTE}
    assert validation.external_evidence_spans == ()


def test_number_supported_only_by_an_external_citation_is_provable() -> None:
    dossier = _with_intent(
        TranslationSection(
            text="Plan confirmation at 2 loading rates.",
            external_citations=(
                ExternalCitation(source_id="notes", quote=SNAPSHOT_RATES),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert "2" not in BRIEF_TEXT
    assert validation.valid
    assert validation.unsupported_numbers == ()


def test_number_supported_by_neither_channel_is_flagged() -> None:
    dossier = _with_intent(
        TranslationSection(
            text="Plan confirmation at 7 loading rates.",
            evidence_quotes=(BRIEF_NEED,),
            external_citations=(
                ExternalCitation(source_id="notes", quote=SNAPSHOT_RATES),
            ),
        )
    )

    validation = verify_dossier(_source(), dossier, _external()).contents[0]

    assert not validation.valid
    assert validation.unsupported_numbers == ("7",)
    assert ValidationErrorCode.UNSUPPORTED_NUMBER in _codes(validation)


def test_load_external_sources_rejects_duplicate_source_ids(tmp_path) -> None:
    pack = tmp_path / "pack.json"
    pack.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "notes",
                        "title": "First snapshot",
                        "locator": "example://frozen-snapshot/first",
                        "text": SNAPSHOT_STIFFENS,
                    },
                    {
                        "source_id": "notes",
                        "title": "Second snapshot",
                        "locator": "example://frozen-snapshot/second",
                        "text": SNAPSHOT_REGRIND,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExternalSourcePackError, match="notes"):
        load_external_sources(pack)


def test_snapshot_digest_is_computed_in_code_not_supplied(tmp_path) -> None:
    pack = tmp_path / "pack.json"
    pack.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "notes",
                        "title": "Illustrative placeholder snapshot",
                        "locator": "example://frozen-snapshot/test-notes",
                        "text": SNAPSHOT_TEXT,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = load_external_sources(pack)

    assert len(loaded) == 1
    assert loaded[0].content_sha256 == hashlib.sha256(
        SNAPSHOT_TEXT.encode("utf-8")
    ).hexdigest()
    with pytest.raises(PydanticValidationError):
        ExternalSourcePackInput.model_validate(
            {
                "sources": [
                    {
                        "source_id": "notes",
                        "title": "Illustrative placeholder snapshot",
                        "locator": "example://frozen-snapshot/test-notes",
                        "text": SNAPSHOT_TEXT,
                        "content_sha256": "0" * 64,
                    }
                ]
            }
        )


def test_no_external_pack_means_no_external_sources() -> None:
    assert load_external_sources(None) == ()


@pytest.mark.parametrize(
    "pack_path",
    sorted(EXAMPLE_PACK_DIR.glob("*.json")),
    ids=lambda path: path.stem,
)
def test_shipped_example_pack_is_loadable_and_labelled_illustrative(
    pack_path: Path,
) -> None:
    sources = load_external_sources(pack_path)

    assert sources
    assert any("ILLUSTRATIVE PLACEHOLDER PACK" in source.text for source in sources)
    for source in sources:
        assert source.locator.startswith("example://")
        assert source.retrieved_at is None
        assert source.content_sha256 == hashlib.sha256(
            source.text.encode("utf-8")
        ).hexdigest()


def test_pipeline_reports_external_evidence_separately_end_to_end(tmp_path) -> None:
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(BRIEF_TEXT, encoding="utf-8")
    pack_path = tmp_path / "external-sources.json"
    pack_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "notes",
                        "title": "Illustrative placeholder snapshot",
                        "locator": "example://frozen-snapshot/test-notes",
                        "retrieved_at": None,
                        "text": SNAPSHOT_TEXT,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    dossier = _with_intent(
        TranslationSection(
            text="Cold handling matters, and general context explains why.",
            evidence_quotes=(BRIEF_NEED,),
            external_citations=(
                ExternalCitation(source_id="notes", quote=SNAPSHOT_TRANSITION),
            ),
        )
    )

    result = run_translation(
        source_path=brief_path,
        output_dir=tmp_path / "run",
        client=ScriptedClient(dossier, _critic()),
        external_sources_path=pack_path,
    )

    assert not result.has_mechanical_errors
    stored = result.report.external_sources
    assert [source.source_id for source in stored] == ["notes"]
    assert stored[0].content_sha256 == hashlib.sha256(
        SNAPSHOT_TEXT.encode("utf-8")
    ).hexdigest()

    intent = result.report.translation_validation.contents[0]
    assert [span.quote for span in intent.evidence_spans] == [BRIEF_NEED]
    assert [span.quote for span in intent.external_evidence_spans] == [
        SNAPSHOT_TRANSITION
    ]
    brief_only = result.report.translation_validation.contents[1]
    assert brief_only.evidence_spans
    assert brief_only.external_evidence_spans == ()

    rendered = result.markdown_path.read_text(encoding="utf-8")
    assert "## External source snapshots" in rendered
    assert "example://frozen-snapshot/test-notes" in rendered
    assert f"> {SNAPSHOT_TRANSITION} (external: Illustrative placeholder snapshot)" in (
        rendered
    )
    assert f"> {BRIEF_NEED} (" in rendered
    assert f"{BRIEF_NEED} (external:" not in rendered
