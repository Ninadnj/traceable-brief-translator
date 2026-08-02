"""Command-line entry point for CompoundX Demo."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from compoundx.external_sources import ExternalSourcePackError
from compoundx.model_client import (
    ModelCallError,
    ModelConfigurationError,
    OpenAIModelClient,
)
from compoundx.pipeline import OutputExistsError, run_translation
from compoundx.source_loader import SourceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compoundx-demo",
        description=(
            "Translate one brief, verify mechanical traceability, run one "
            "critic pass, and write a human-review report."
        ),
    )
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--external-sources",
        type=Path,
        help="Optional JSON pack of explicit external-source text snapshots",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("COMPOUNDX_DEMO_MODEL"),
        help="OpenAI model ID (or set COMPOUNDX_DEMO_MODEL)",
    )
    parser.add_argument(
        "--critic-model",
        default=os.environ.get("COMPOUNDX_DEMO_CRITIC_MODEL"),
        help=(
            "Optional distinct OpenAI model for the critic "
            "(or set COMPOUNDX_DEMO_CRITIC_MODEL)"
        ),
    )
    parser.add_argument("--max-output-tokens", type=int, default=12_000)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        selected_model = arguments.model or ""
        client = OpenAIModelClient(
            model=selected_model,
            max_output_tokens=arguments.max_output_tokens,
        )
        critic_client = (
            OpenAIModelClient(
                model=arguments.critic_model,
                max_output_tokens=arguments.max_output_tokens,
            )
            if arguments.critic_model
            and arguments.critic_model != selected_model
            else None
        )
        result = run_translation(
            source_path=arguments.brief,
            output_dir=arguments.output_dir,
            client=client,
            critic_client=critic_client,
            external_sources_path=arguments.external_sources,
        )
    except (
        ModelCallError,
        ModelConfigurationError,
        ExternalSourcePackError,
        OSError,
        ValidationError,
        OutputExistsError,
        SourceError,
    ) as error:
        logging.error("%s", error)
        return 1

    print(result.markdown_path)
    return 2 if result.has_mechanical_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
