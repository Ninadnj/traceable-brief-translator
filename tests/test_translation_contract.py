from __future__ import annotations

import hashlib
from pathlib import Path

from compoundx.models import SourceDocument
from compoundx.schemas import (
    CriticAssessment,
    CriticIssueType,
    CriticOutput,
    DossierSection,
    SectionFinding,
    SourcePageSpan,
    TechnicalDossier,
    TraceablePoint,
    TranslationSection,
    SupportAssessment,
    ValidationErrorCode,
)
from compoundx.translator import _prompt_text
from compoundx.verifier import verify_critic, verify_dossier
from compoundx.source_loader import load_source_document


SOURCE_TEXT = (
    "Reliability is the first priority.\n"
    "The housing protects and retains the braking mechanism.\n"
    "No controlled cold-impact protocol has been defined.\n"
    "Approximately 15% mass reduction would be valuable.\n"
    "At least 30% recycled content is expected without reducing durability.\n"
)


def _source(text: str = SOURCE_TEXT) -> SourceDocument:
    return SourceDocument(
        source_name="brief.md",
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
    )


def _dossier() -> TechnicalDossier:
    return TechnicalDossier(
        product_intent=TranslationSection(
            text=(
                "Improve reliability first; mass reduction is valuable but "
                "secondary."
            ),
            evidence_quotes=(
                "Reliability is the first priority.",
                "Approximately 15% mass reduction would be valuable.",
            ),
            uncertainties=("No reliability comparison method is defined.",),
        ),
        component_function=TranslationSection(
            text="The housing must protect and retain the braking mechanism.",
            evidence_quotes=(
                "The housing protects and retains the braking mechanism.",
            ),
        ),
        performance_to_validate=TranslationSection(
            text=(
                "The complete housing should maintain protection and retention "
                "after a defined cold-impact sequence."
            ),
            evidence_quotes=(
                "The housing protects and retains the braking mechanism.",
                "No controlled cold-impact protocol has been defined.",
            ),
            assumptions=(
                "The historical concern needs a future component-level check.",
            ),
            uncertainties=("The protocol and acceptance criteria are missing.",),
        ),
        material_relevant_criteria=TranslationSection(
            text=(
                "Cold-impact cracking behaviour and recycled-feedstock "
                "consistency may influence the required performance."
            ),
            evidence_quotes=(
                "No controlled cold-impact protocol has been defined.",
                "At least 30% recycled content is expected without reducing durability.",
            ),
            uncertainties=("No material-property target is supported.",),
        ),
        missing_information=(
            TraceablePoint(
                text="Define the cold-impact protocol and acceptance criteria.",
                evidence_quotes=(
                    "No controlled cold-impact protocol has been defined.",
                ),
            ),
        ),
        conflicts_and_tradeoffs=(
            TraceablePoint(
                text="Balance recycled content against durability.",
                evidence_quotes=(
                    "At least 30% recycled content is expected without reducing durability.",
                ),
            ),
        ),
    )


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


def test_dossier_is_six_sections_without_item_graph() -> None:
    dossier = _dossier()
    dumped = dossier.model_dump()

    assert set(dumped) == {
        "product_intent",
        "component_function",
        "performance_to_validate",
        "material_relevant_criteria",
        "missing_information",
        "conflicts_and_tradeoffs",
    }
    assert not _contains_key(dumped, "id")
    assert not _contains_key(dumped, "parent_id")


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, key) for item in value)
    return False


def test_complete_dossier_has_six_valid_traceable_contents() -> None:
    validation = verify_dossier(_source(), _dossier())

    assert validation.valid_content_count == 6
    assert validation.invalid_content_count == 0
    assert [content.owner for content in validation.contents] == [
        "product_intent",
        "component_function",
        "performance_to_validate",
        "material_relevant_criteria",
        "missing_information[1]",
        "conflicts_and_tradeoffs[1]",
    ]


def test_only_numbers_present_in_verified_evidence_are_allowed() -> None:
    dossier = _dossier().model_copy(
        update={
            "product_intent": _dossier().product_intent.model_copy(
                update={"text": "A derived target of 69.7 g would be useful."}
            )
        }
    )

    validation = verify_dossier(_source(), dossier).contents[0]

    assert validation.unsupported_numbers == ("69.7",)
    assert ValidationErrorCode.UNSUPPORTED_NUMBER in {
        error.code for error in validation.errors
    }


def test_a_fabricated_number_fused_to_its_unit_cannot_bypass_the_numeric_rule() -> None:
    # The <value><unit> form is how a target is most often written, so it must be
    # checked like any other number. Classifying it as an identifier would exclude
    # it on both sides of the comparison and leave the rule silently unenforced.
    dossier = _dossier().model_copy(
        update={
            "product_intent": _dossier().product_intent.model_copy(
                update={"text": "A derived target of 250g is proposed."}
            )
        }
    )

    validation = verify_dossier(_source(), dossier).contents[0]

    assert validation.unsupported_numbers == ("250",)
    assert not validation.valid


def test_a_hyphenated_range_in_evidence_supports_either_bound() -> None:
    # A hyphen between two numbers separates them; it does not sign the second.
    # Otherwise evidence quoting a range would fail to support its own upper bound.
    text = "The qualified load range is 10-15 kg.\n"
    dossier = _dossier().model_copy(
        update={
            "product_intent": _dossier().product_intent.model_copy(
                update={
                    "text": "Validation should confirm behaviour at 15 kg.",
                    "evidence_quotes": ("The qualified load range is 10-15 kg.",),
                    "uncertainties": (),
                }
            )
        }
    )

    validation = verify_dossier(_source(text), dossier).contents[0]

    assert validation.unsupported_numbers == ()
    assert validation.valid


def test_written_numbers_do_not_create_prose_unit_false_positives() -> None:
    text = "Two of five housings cracked during an informal observation.\n"
    dossier = _dossier().model_copy(
        update={
            "performance_to_validate": TranslationSection(
                text=(
                    "Two of five housings cracked, so controlled future "
                    "performance remains to be defined."
                ),
                evidence_quotes=(
                    "Two of five housings cracked during an informal observation.",
                ),
            )
        }
    )

    validation = verify_dossier(_source(text), dossier).contents[2]

    assert validation.valid


def test_ambiguous_quote_is_a_processing_error() -> None:
    text = "Repeated evidence. Repeated evidence.\n"
    dossier = _dossier().model_copy(
        update={
            "product_intent": TranslationSection(
                text="Preserve the supplied need.",
                evidence_quotes=("Repeated evidence.",),
            )
        }
    )

    validation = verify_dossier(_source(text), dossier).contents[0]

    assert ValidationErrorCode.AMBIGUOUS_QUOTE in {
        error.code for error in validation.errors
    }


def test_pdf_layout_normalization_maps_to_exact_raw_page_span() -> None:
    text = 'The visible surface must remain “black‑and‑low-gloss” and\nrobust.\n'
    quote = 'The visible surface must remain "black-and-low-gloss" and robust.'
    dossier = _dossier().model_copy(
        update={
            "product_intent": TranslationSection(
                text="Preserve the specified visible finish and perceived robustness.",
                evidence_quotes=(quote,),
            )
        }
    )

    validation = verify_dossier(
        _source(text),
        dossier,
        page_spans=(SourcePageSpan(page_number=2, start=0, end=len(text) - 1),),
    ).contents[0]

    assert validation.valid
    span = validation.evidence_spans[0]
    assert span.quote == quote
    assert span.source_quote == text[:-1]
    assert span.page_number == 2
    assert text[span.start:span.end] == span.source_quote


def test_normalized_matching_does_not_accept_changed_wording() -> None:
    text = "The surface must remain black and low-gloss.\n"
    dossier = _dossier().model_copy(
        update={
            "product_intent": TranslationSection(
                text="Preserve the surface requirement.",
                evidence_quotes=("The surface should remain black and low-gloss.",),
            )
        }
    )

    validation = verify_dossier(_source(text), dossier).contents[0]

    assert not validation.valid
    assert [error.code for error in validation.errors] == [
        ValidationErrorCode.MISSING_QUOTE
    ]


def test_normalized_duplicate_quotes_remain_ambiguous() -> None:
    text = "The finish is low-gloss. The finish is\nlow-gloss.\n"
    dossier = _dossier().model_copy(
        update={
            "product_intent": TranslationSection(
                text="Preserve the finish.",
                evidence_quotes=("The finish is low-gloss.",),
            )
        }
    )

    validation = verify_dossier(_source(text), dossier).contents[0]

    assert not validation.valid
    assert validation.errors[0].code is ValidationErrorCode.AMBIGUOUS_QUOTE


def test_pdf_loader_records_reversible_page_ranges() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "demo-results"
        / "roller-skate"
        / "source"
        / "junior_roller_skate_target_brief.pdf"
    )

    loaded = load_source_document(fixture)

    assert loaded.page_count == 2
    assert [page.page_number for page in loaded.page_spans] == [1, 2]
    assert loaded.page_spans[0].start == 0
    assert loaded.page_spans[0].end + 2 == loaded.page_spans[1].start
    for page in loaded.page_spans:
        assert loaded.document.text[page.start:page.end].strip()


def test_critic_reviews_each_section_exactly_once() -> None:
    validation = verify_critic(_source(), _critic())

    assert not validation.errors


def test_critic_duplicate_and_missing_section_are_visible() -> None:
    findings = list(_critic().findings)
    findings[-1] = findings[0]
    critic = CriticOutput(findings=tuple(findings))

    validation = verify_critic(_source(), critic)
    codes = {error.code for error in validation.errors}

    assert ValidationErrorCode.DUPLICATE_SECTION_FINDING in codes
    assert ValidationErrorCode.MISSING_SECTION_FINDING in codes


def test_current_critic_contract_separates_translation_from_source_evidence() -> None:
    findings = list(_critic().findings)
    findings[0] = SectionFinding(
        section=DossierSection.PRODUCT_INTENT,
        assessment=CriticAssessment.REVISE,
        support_assessment=SupportAssessment.PARTIAL,
        issue_types=(CriticIssueType.QUALIFIER_FORCE,),
        translated_excerpts=("mass reduction is valuable but secondary",),
        explanation="The wording needs a more exact statement of priority and force.",
        suggested_revision="Preserve the source qualifier and explicit priority.",
        evidence_quotes=(
            "Approximately 15% mass reduction would be valuable.",
        ),
        human_review_required=True,
    )
    critic = _critic().model_copy(update={"findings": tuple(findings)})
    dossier = _dossier()

    validation = verify_critic(
        _source(),
        critic,
        dossier=dossier,
        dossier_validation=verify_dossier(_source(), dossier),
        require_current_contract=True,
    )

    assert validation.errors == ()


def test_critic_excerpt_must_exist_in_the_translated_section() -> None:
    findings = list(_critic().findings)
    findings[0] = SectionFinding(
        section=DossierSection.PRODUCT_INTENT,
        assessment=CriticAssessment.REVISE,
        support_assessment=SupportAssessment.PARTIAL,
        issue_types=(CriticIssueType.EVIDENCE_SUPPORT,),
        translated_excerpts=("Text invented by the critic.",),
        explanation="The cited evidence does not support this wording.",
        suggested_revision="Remove the unsupported wording.",
        evidence_quotes=("Reliability is the first priority.",),
        human_review_required=True,
    )
    critic = _critic().model_copy(update={"findings": tuple(findings)})

    validation = verify_critic(
        _source(),
        critic,
        dossier=_dossier(),
        dossier_validation=verify_dossier(_source(), _dossier()),
        require_current_contract=True,
    )

    assert ValidationErrorCode.MISSING_TRANSLATED_EXCERPT in {
        error.code for error in validation.errors
    }


def test_critic_must_escalate_a_mechanically_invalid_section() -> None:
    dossier = _dossier().model_copy(
        update={
            "product_intent": _dossier().product_intent.model_copy(
                update={"text": "A fabricated target of 69.7 g is proposed."}
            )
        }
    )
    dossier_validation = verify_dossier(_source(), dossier)

    validation = verify_critic(
        _source(),
        _critic(),
        dossier=dossier,
        dossier_validation=dossier_validation,
        require_current_contract=True,
    )

    assert ValidationErrorCode.UNRESOLVED_MECHANICAL_ERROR in {
        error.code for error in validation.errors
    }


def test_prompts_require_synthesis_and_section_level_critique() -> None:
    translator = _prompt_text("translator.txt")
    critic = _prompt_text("critic.txt")

    assert "one concise technical dossier" in translator
    assert "Historical observations explain why validation is needed" in translator
    assert "Do not assign IDs" in translator
    assert "Do not calculate derived values" in translator
    assert "silently audit the entire brief once for completeness" in translator
    assert "Never upgrade a preference into a hard acceptance limit" in translator
    assert "A product constraint remains a constraint" in translator
    assert "embedded claim was supplied" in translator
    assert "quantitative field evidence materially establishes" in translator
    assert "partial proxy such as unit material price" in translator
    assert "exactly one SectionFinding" in critic
    assert "not as a collection of atomic facts" in critic
    assert "lists a known source value or stated preference as missing" in critic
    assert "copied numerical target as validated" in critic
    assert "partial price or process proxy" in critic
    assert "contract_version exactly as `compoundx.critic.v2`" in critic
    assert "Never put translator wording" in critic
    assert "Mechanically invalid" in critic or "mechanically invalid" in critic
    assert "same-object evidence audit" in translator
    assert "Citation coverage is local and clause-level" in translator
    assert "explicitly including zero" in translator
    assert "Use direct only when every material claim" in critic
    assert "never translate an unstated limit as a zero limit" in critic
