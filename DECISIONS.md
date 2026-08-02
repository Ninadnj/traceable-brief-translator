# CompoundX Demo decisions

## 1. The six-section dossier is the primary contract

The product brief is translated into product intent, component function,
performance to validate, material-relevant criteria, missing information, and
conflicts and trade-offs. Evidence, assumptions, and uncertainty stay visibly
separate from the synthesized engineering text.

The schema provides the reasoning order. The model does not create IDs, source
offsets, parent graphs, or calculations for the central translation.

## 2. Two standard model passes, one bounded repair, no agent loop

The translator produces the dossier. A separate section-level critic challenges
its support, qualification, completeness, and trade-offs. When deterministic
validation rejects the initial translation, exactly one repair call receives the
typed errors and returns a complete replacement dossier. The report retains the
initial dossier, initial validation and both call records, then revalidates the
replacement from scratch. There are no autonomous loops, framework orchestration
layers, silent rewrites, or repeated retries.

Critic findings are reviewable proposals, not deterministic proof.

## 3. Mechanical verification remains deterministic

Code resolves citations against the preserved source text, records exact raw
slices and PDF pages, validates required sections and schemas, and blocks
unsupported numbers. Narrow normalization handles PDF whitespace, quotation-mark,
and hyphen variation without permitting paraphrases or fuzzy semantic matches.

One artifact of extraction is genuinely ambiguous rather than merely noisy. A
hyphen at a line break may belong to a compound word split by justification or to
a single word broken mid-syllable, and nothing in the text separates the two
cases. Rather than guess, each such hyphen is marked optional and resolved by the
quote itself, one site at a time. Guessing globally would fail twice over: it
would reject a quote that has to resolve two sites differently, and — worse — a
statement appearing once broken and once intact would be counted once and
certified unique when it is genuinely ambiguous.

Only a hyphen a typesetter actually breaks words on is optional. An en or em dash
at a line end is content: the break is still dropped, but the dash stays
mandatory, because no one splits a word on an em dash and treating one as
droppable would invent ambiguity instead of absorbing it.

The cost is a bounded class of false positive: where dropping a hyphen produces a
different real word, a broken `re-\nsign` also matches `resign`. No text-only rule
can separate those, the literal reading is preferred, and the renderer shows the
raw source slice rather than the model's quote, so the join stays visible to the
reviewer. Compounds split around a digit, such as `5-\nyear`, are deliberately not
joined, because that rule is what keeps numeric ranges out.

An occurrence is counted per position in the source, not per parse. Where two
readings of the same characters both satisfy a quote, that is one citation, not an
`ambiguous_quote`. Differential fuzzing against an exhaustive oracle over 14
million source/quote pairs found no occurrence missed and none invented; the only
differences were end offsets within a single shared position. Matching is linear
in practice and quadratic in the worst case on pathologically repetitive text,
which a real brief does not produce.

These checks prove traceability and numeric provenance. They do not prove that an
engineering interpretation is correct or complete.

## 4. Credentials are ephemeral and artifacts are durable

The Streamlit UI accepts an OpenAI API key for the browser session and never
writes it to disk. Every submitted source is saved before model use, and completed
reports are written into new, non-overwriting run directories.

The repository contains only the two curated demonstrations needed for review:
the roller-skate target case and garden-trimmer altered case.

## 5. Semantic evaluation is separate from production translation

The repository retains a human-authored eight-criterion rubric and a strict
semantic-evaluation schema. Each refreshed demonstration now has one disclosed
`model_assisted` evaluation of its canonical finalized human-review JSON. The
evaluation records a run-relative path and SHA-256 for that reviewed artifact;
both cases score `[5, 5, 5, 4, 5, 5, 5, 4]` (mean 4.75/5) and all their
case-specific adversarial checks pass.

This remains supporting evidence, not an acceptance gate or an independent
semantic approval. It runs after finalization, cannot alter the review chain,
and does not independently confirm the named reviewer's decision. The
section-level critic is part of production translation; rubric evaluation is a
separate one-way child of the finalized review.

## 6. Human review is section-level and immutable

A reviewer approves, corrects, or flags each section independently. Corrections,
removals, and additions require rationales. Each save is parent-linked and
revalidated against the accepted source and report. Final status is derived from
the six decisions and validation result.

The final JSON is the complete audit artifact. Markdown and PDF are deterministic
presentations of the same reviewed dossier, with a sidecar manifest containing
their hashes.

The two published cases each contain six immutable `save_section` snapshots and
one `finalize` snapshot, all by Nina Doinjashvili and all linked to the same
accepted-report and source hashes. The terminal status is
`approved_with_corrections`. Adding one reviewed missing-information item per
case increases the mechanically valid content counts from 12 to 13 for
roller-skate and from 11 to 12 for garden-trimmer; both reviewed dossiers retain
zero validation errors. Final JSON, Markdown, PDF and manifest files are shipped.

Review directories use
`<timestamp>-<action>-<section-or-terminal-status>-<hash-prefix>`. The finalized
review is terminal: neither a later section snapshot nor its supporting
evaluation may reopen or extend that chain.

## 7. External knowledge is explicit and bounded

The core workflow uses the supplied brief. Optional external knowledge must come
from an explicit, caller-supplied snapshot pack whose text is frozen and hashed
before model use. External claims require exact snapshot citations, are verified
against the frozen snapshot rather than the brief, and are reported through a
separate evidence channel so brief-derived evidence is never mixed with
introduced knowledge.

Live retrieval is deliberately out of scope. A demo that fetches the open web at
presentation time adds a network failure mode and an unpinned input, and the
provenance question the case asks — can a reader tell supplied facts from
introduced ones — is answered by the frozen-snapshot boundary, not by the
fetching mechanism. Adding a retrieval front end later only has to produce the
same pack format.

The refreshed reports state the current limitation directly: snapshots are
caller supplied and exact-quote verified. The shipped review chains are tied to
the refreshed accepted-report hashes, and their separate evaluations are tied to
the exact finalized reviewed-JSON hashes. Superseded review chains are not
shipped in this clean recovered repository and cannot be mistaken for review of
the new reports.

## Known limitations

- Exact citations do not prove semantic entailment or engineering correctness.
- The system cannot prove physical root cause or final material suitability.
- Model critic findings are not independent expert evaluation.
- Human approval can still be wrong.
- Scanned PDFs require OCR before use.
