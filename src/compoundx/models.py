"""Product-agnostic primitives: the loaded source and lexical quantity types."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompoundXModel(BaseModel):
    """Common Pydantic configuration for stored artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NumericTokenKind(str, Enum):
    DIGIT = "digit"
    WRITTEN = "written"
    IDENTIFIER = "identifier"


class QuantityKind(str, Enum):
    SCALAR = "scalar"
    RANGE = "range"
    TOLERANCE = "tolerance"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    WRITTEN = "written"


class QuantityQualifier(str, Enum):
    APPROXIMATE = "approximate"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    COMPARATIVE = "comparative"


class SourceDocument(CompoundXModel):
    source_name: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str


class NumericToken(CompoundXModel):
    raw_text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    kind: NumericTokenKind
    value: Decimal

    @model_validator(mode="after")
    def validate_token_length(self) -> NumericToken:
        if self.end <= self.start:
            raise ValueError("numeric token end must be greater than start")
        return self


class Quantity(CompoundXModel):
    raw_text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    kind: QuantityKind
    values: tuple[Decimal, ...]
    unit: str | None = None
    upper_unit: str | None = None
    currency: str | None = None
    qualifier: QuantityQualifier | None = None

    @model_validator(mode="after")
    def validate_quantity_shape(self) -> Quantity:
        expected_values = (
            2
            if self.kind in (QuantityKind.RANGE, QuantityKind.TOLERANCE)
            else 1
        )
        if len(self.values) != expected_values:
            raise ValueError(
                f"{self.kind.value} quantity requires {expected_values} value(s)"
            )
        if self.end <= self.start:
            raise ValueError("quantity end must be greater than start")
        return self
