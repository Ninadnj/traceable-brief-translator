"""Compact, product-agnostic contracts for a six-section technical dossier."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from compoundx.models import CompoundXModel


class DossierSection(str, Enum):
    PRODUCT_INTENT = "product_intent"
    COMPONENT_FUNCTION = "component_function"
    PERFORMANCE_TO_VALIDATE = "performance_to_validate"
    MATERIAL_RELEVANT_CRITERIA = "material_relevant_criteria"
    MISSING_INFORMATION = "missing_information"
    CONFLICTS_AND_TRADEOFFS = "conflicts_and_tradeoffs"


PRIMARY_SECTIONS = (
    DossierSection.PRODUCT_INTENT,
    DossierSection.COMPONENT_FUNCTION,
    DossierSection.PERFORMANCE_TO_VALIDATE,
    DossierSection.MATERIAL_RELEVANT_CRITERIA,
)


class SourceFormat(str, Enum):
    TEXT = "text"
    PDF = "pdf"


class SourcePageSpan(CompoundXModel):
    """One PDF page's exact range in the persisted extracted source text."""

    page_number: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_offsets(self) -> SourcePageSpan:
        if self.end < self.start:
            raise ValueError("page span end must not precede start")
        return self


class ExternalUse(str, Enum):
    SCREENING = "screening"
    TRADE_OFF = "trade_off"
    VALIDATION_SUGGESTION = "validation_suggestion"


class ExternalSource(CompoundXModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    retrieved_at: datetime | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1)


class ExternalCitation(CompoundXModel):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class TraceableContent(CompoundXModel):
    evidence_quotes: tuple[str, ...] = ()
    external_citations: tuple[ExternalCitation, ...] = ()

    @model_validator(mode="after")
    def at_least_one_source(self) -> TraceableContent:
        if not self.evidence_quotes and not self.external_citations:
            raise ValueError("traceable content requires at least one citation")
        return self


class TranslationSection(TraceableContent):
    """One concise translation level, with evidence kept separate from synthesis."""

    text: str = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


class TraceablePoint(TraceableContent):
    text: str = Field(min_length=1)


class TechnicalDossier(CompoundXModel):
    product_intent: TranslationSection
    component_function: TranslationSection
    performance_to_validate: TranslationSection
    material_relevant_criteria: TranslationSection
    missing_information: tuple[TraceablePoint, ...] = Field(min_length=1)
    conflicts_and_tradeoffs: tuple[TraceablePoint, ...] = Field(min_length=1)


class CriticAssessment(str, Enum):
    SUPPORTED = "supported"
    REVISE = "revise"
    UNCERTAIN = "uncertain"


class SupportAssessment(str, Enum):
    """The critic's semantic judgement, kept separate from its recommended action."""

    DIRECT = "direct"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class CriticIssueType(str, Enum):
    """Small, reviewable taxonomy for why a section needs attention."""

    EVIDENCE_SUPPORT = "evidence_support"
    QUALIFIER_FORCE = "qualifier_force"
    OBSERVATION_AS_REQUIREMENT = "observation_as_requirement"
    OMISSION = "omission"
    SECTION_BOUNDARY = "section_boundary"
    MECHANICAL_VALIDATION = "mechanical_validation"
    COMPONENT_USEFULNESS = "component_usefulness"


class SectionFinding(CompoundXModel):
    section: DossierSection
    assessment: CriticAssessment
    support_assessment: SupportAssessment | None = None
    issue_types: tuple[CriticIssueType, ...] = ()
    # These are exact excerpts from the translator output being criticised. They
    # are deliberately separate from evidence_quotes, which may contain only
    # exact source text.
    translated_excerpts: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)
    suggested_revision: str | None = Field(default=None, min_length=1)
    evidence_quotes: tuple[str, ...] = ()
    human_review_required: bool


class CriticOutput(CompoundXModel):
    contract_version: Literal["compoundx.critic.v2"] | None = None
    findings: tuple[SectionFinding, ...] = Field(min_length=6, max_length=6)
    additional_missing_information: tuple[TraceablePoint, ...] = ()
    additional_tradeoffs: tuple[TraceablePoint, ...] = ()
    additional_open_questions: tuple[str, ...] = ()


class ValidationErrorCode(str, Enum):
    MISSING_QUOTE = "missing_quote"
    AMBIGUOUS_QUOTE = "ambiguous_quote"
    UNSUPPORTED_NUMBER = "unsupported_number"
    UNKNOWN_EXTERNAL_SOURCE = "unknown_external_source"
    MISSING_EXTERNAL_QUOTE = "missing_external_quote"
    AMBIGUOUS_EXTERNAL_QUOTE = "ambiguous_external_quote"
    MISSING_SECTION_FINDING = "missing_section_finding"
    DUPLICATE_SECTION_FINDING = "duplicate_section_finding"
    INVALID_CRITIC_CONTRACT = "invalid_critic_contract"
    MISSING_TRANSLATED_EXCERPT = "missing_translated_excerpt"
    UNRESOLVED_MECHANICAL_ERROR = "unresolved_mechanical_error"


class ValidationError(CompoundXModel):
    code: ValidationErrorCode
    message: str = Field(min_length=1)
    field: str | None = None
    value: str | None = None


class EvidenceSpan(CompoundXModel):
    # `quote` is the model-supplied citation. `source_quote` is the exact raw
    # slice recovered after normalized matching.
    quote: str = Field(min_length=1)
    source_quote: str | None = Field(default=None, min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page_number: int | None = Field(default=None, ge=1)


class ExternalEvidenceSpan(CompoundXModel):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    source_quote: str | None = Field(default=None, min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ContentValidation(CompoundXModel):
    owner: str = Field(min_length=1)
    valid: bool
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    external_evidence_spans: tuple[ExternalEvidenceSpan, ...] = ()
    errors: tuple[ValidationError, ...] = ()
    unsupported_numbers: tuple[str, ...] = ()


class DossierValidation(CompoundXModel):
    contents: tuple[ContentValidation, ...]

    @property
    def valid_content_count(self) -> int:
        return sum(content.valid for content in self.contents)

    @property
    def invalid_content_count(self) -> int:
        return len(self.contents) - self.valid_content_count


class CriticEvidenceSpan(CompoundXModel):
    owner: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    source_quote: str | None = Field(default=None, min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page_number: int | None = Field(default=None, ge=1)


class CriticExternalEvidenceSpan(CompoundXModel):
    owner: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    source_quote: str | None = Field(default=None, min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class CriticValidation(CompoundXModel):
    evidence_spans: tuple[CriticEvidenceSpan, ...] = ()
    external_evidence_spans: tuple[CriticExternalEvidenceSpan, ...] = ()
    errors: tuple[ValidationError, ...] = ()


class ModelCallInfo(CompoundXModel):
    model: str = Field(min_length=1)
    response_model: str = Field(min_length=1)
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)


class SourceSummary(CompoundXModel):
    name: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    character_count: int = Field(ge=0)
    source_format: SourceFormat
    page_count: int | None = Field(default=None, ge=1)


class ReviewReport(CompoundXModel):
    generated_at: datetime
    source: SourceSummary
    source_text: str
    source_pages: tuple[SourcePageSpan, ...] = ()
    external_sources: tuple[ExternalSource, ...] = ()
    translator_call: ModelCallInfo
    initial_translator_call: ModelCallInfo | None = None
    initial_translation: TechnicalDossier | None = None
    initial_translation_validation: DossierValidation | None = None
    translator_repair_call: ModelCallInfo | None = None
    translator_repair_error: str | None = None
    translation: TechnicalDossier
    translation_validation: DossierValidation
    critic_call: ModelCallInfo | None = None
    critic: CriticOutput | None = None
    critic_validation: CriticValidation | None = None
    critic_error: str | None = None
    known_limitations: tuple[str, ...]
