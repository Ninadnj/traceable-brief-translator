# AGENTS.md

## Project

This is the standalone CompoundX Demo technical-case prototype. It translates one
incomplete product brief into a six-section, traceable engineering dossier.

## Core principle

AI proposes semantic interpretations. Deterministic code validates provenance,
references, quantities, schemas and known safety invariants. Humans review
unresolved engineering judgement.

## Scope

- Keep this repository focused on the single current implementation.
- Prefer simple, explicit Python and direct SDK use.
- Do not introduce agent frameworks, RAG, vector databases, graph databases or
  production infrastructure without explicit approval.
- Do not encode product-specific facts into prompts, schemas or runtime logic.
- Preserve source text, validation diagnostics, rejection reasons and immutable
  review history required by the current demonstration.
- API keys remain in environment variables or session memory and are never
  persisted.

## Verification

Use `pytest`. Report commands, passed tests, failed tests and known limitations
before declaring work complete.
