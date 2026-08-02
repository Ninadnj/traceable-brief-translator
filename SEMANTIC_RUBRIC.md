# V2 semantic evaluation rubric

This human-authored rubric evaluates whether a mechanically traceable dossier is
useful and correctly levelled for engineering review. A score may be completed
by an independent human or as a disclosed model-assisted evaluation. The
evaluation artifact must identify which method was used. Model-assisted scoring
is not independent human review and must never be presented as such.

The rubric remains separate from the translation pipeline. It does not become a
third production model pass and it does not claim deterministic semantic proof.
The saved artifact under evaluation may be the accepted model report or the
canonical finalized human-review JSON. When it is a finalized review, evidence
paths address `reviewed_dossier` (and, where relevant, `sections` and
`validation`) rather than the base report's `translation` field. `report_path`
must identify that saved artifact and `report_sha256` must hash the exact bytes
at that path.

In the published demonstrations the rubric is explicitly `model_assisted` and
targets each run's finalized `review.json` using a run-relative path. It is
written afterward as `evaluation/evaluation.json` plus a deterministic Markdown
rendering. That final-review-to-evaluation link is one-way: the evaluation is
supporting evidence only and cannot change eligibility, review decisions,
terminal status, or review lineage.

## Scoring anchors

| Score | Meaning |
| ---: | --- |
| 1 | Harmful or fundamentally wrong; it promotes unsupported conclusions. |
| 2 | Major revision required; important reasoning levels or constraints are wrong. |
| 3 | Useful but incomplete; material omissions or qualification errors remain. |
| 4 | Strong and decision-useful; only minor wording or completeness improvements remain. |
| 5 | Complete, correctly qualified, concise and ready for human engineering discussion. |

## Criteria

1. Product intent preserves priorities and distinguishes objectives,
   constraints and preferences.
2. Component function is technically clear and does not substitute material
   properties or current design details for function.
3. Performance is future-facing, observable and component- or product-level;
   observations remain rationale rather than acceptance criteria.
4. Material criteria are cautious, relevant and non-prescriptive; weak source
   claims remain visibly weak.
5. Missing information contains the unresolved definitions and evidence needed
   for the next decision without prescribing unsupported actions.
6. Conflicts and trade-offs preserve competing priorities, causal ambiguity and
   evidence limitations.
7. Evidence is traceable: exact raw source slices, page numbers and numeric
   provenance are mechanically valid.
8. The output is concise and readable as one coherent engineering dossier.

## Passing rule

A case passes only when every criterion scores at least 4/5 and every
case-specific adversarial check passes. `overall_pass` is calculated from those
inputs and cannot be supplied by the evaluator. The completed evaluation must
cite fields in the saved artifact used for each judgement. A model-assisted
evaluation performed after a human review does not become human rubric scoring
and does not independently confirm or countersign that review. Neither form of
rubric result proves that a design or material is safe or suitable.
