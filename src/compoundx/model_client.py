"""One small OpenAI structured-output client for both model passes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Generic, Protocol, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import ValidationError as PydanticValidationError

from compoundx.models import CompoundXModel
from compoundx.schemas import ModelCallInfo


StructuredModel = TypeVar("StructuredModel", bound=CompoundXModel)


@dataclass(frozen=True)
class ModelCallResult(Generic[StructuredModel]):
    output: StructuredModel
    info: ModelCallInfo


class StructuredModelClient(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_model: type[StructuredModel],
    ) -> ModelCallResult[StructuredModel]:
        """Return one provider-parsed structured response."""


class OpenAIModelClient:
    """OpenAI-only client intentionally scoped to this prototype."""

    def __init__(
        self,
        *,
        model: str,
        max_output_tokens: int = 12_000,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not model.strip():
            raise ModelConfigurationError("a non-empty OpenAI model ID is required")
        if max_output_tokens < 1:
            raise ModelConfigurationError("max_output_tokens must be positive")
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None and not resolved_key:
            raise ModelConfigurationError(
                "OPENAI_API_KEY is required to run compoundx-demo"
            )
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.client = client or OpenAI(api_key=resolved_key)

    def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_model: type[StructuredModel],
    ) -> ModelCallResult[StructuredModel]:
        started = perf_counter()
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=user_message,
                text_format=response_model,
                max_output_tokens=self.max_output_tokens,
                store=False,
            )
        except OpenAIError as error:
            raise ModelCallError(f"OpenAI request failed: {error}") from error
        duration = perf_counter() - started

        status = _string_value(getattr(response, "status", None))
        if status not in (None, "completed"):
            raise ModelCallError(
                f"OpenAI response did not complete successfully: {status}"
            )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ModelCallError(
                "OpenAI response contained no parsed structured output"
            )
        try:
            output = (
                parsed
                if isinstance(parsed, response_model)
                else response_model.model_validate(parsed)
            )
        except PydanticValidationError as error:
            raise ModelCallError(
                "OpenAI response did not match the requested structured output"
            ) from error
        usage = getattr(response, "usage", None)
        return ModelCallResult(
            output=output,
            info=ModelCallInfo(
                model=self.model,
                response_model=response_model.__name__,
                response_id=_string_value(getattr(response, "id", None)),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                duration_seconds=duration,
            ),
        )


class ModelConfigurationError(ValueError):
    """Raised when the single supported model cannot be configured."""


class ModelCallError(RuntimeError):
    """Raised when a structured model call fails."""


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
