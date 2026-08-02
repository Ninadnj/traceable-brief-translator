# External knowledge

The case asks that a reader be able to tell information supplied in the target
brief from knowledge introduced by the system. The answer is two evidence
channels, verified independently, that never cross-validate.

## The boundary

| | Brief channel | External channel |
| --- | --- | --- |
| Cited as | `evidence_quotes` | `external_citations` (`source_id` + `quote`) |
| Verified against | the preserved brief text | the frozen snapshot named by `source_id` |
| Reported in | `evidence_spans` (with PDF page) | `external_evidence_spans` (with `source_id`) |
| Failure codes | `missing_quote`, `ambiguous_quote` | `unknown_external_source`, `missing_external_quote`, `ambiguous_external_quote` |

The guarantee is directional, and it is the whole point: a sentence that exists in
the brief does **not** satisfy an external citation, and a sentence that exists
only in a snapshot does **not** satisfy a brief citation. The same string cited in
the wrong channel fails while verifying cleanly in the right one.

Numeric provenance spans both channels but stays object-local: a number must
appear in a citation attached to *that same object*, from either channel. A number
backed only by a snapshot is provable; one backed by neither is flagged
`unsupported_number`.

## Why live retrieval is out of scope

Fetching the open web at presentation time adds a network failure mode and an
unpinned input, and it does not answer the provenance question. What makes
introduced knowledge auditable is that its text is frozen, hashed and quoted
exactly — not the mechanism that fetched it. A retrieval front end added later
only has to emit this same pack format.

## Pack format

One JSON object with a non-empty `sources` array. `retrieved_at` is optional; the
rest are required and non-blank. Unknown keys are rejected, so a caller cannot
supply `content_sha256` — the loader computes it in code from the snapshot text.
Duplicate `source_id` values are rejected outright.

```json
{
  "sources": [
    {
      "source_id": "illustrative_polymer_low_temperature_notes",
      "title": "Illustrative placeholder notes: polymers at reduced temperature",
      "locator": "example://frozen-snapshot/polymer-cold-temperature-notes",
      "retrieved_at": null,
      "text": "ILLUSTRATIVE PLACEHOLDER - NOT A RETRIEVED SOURCE. Polymers do not behave at reduced temperature the way they behave at room temperature. ..."
    }
  ]
}
```

`examples/external-sources/illustrative-polymer-notes.json` is a working pack whose
content is **illustrative placeholder material written by hand for this demo**.
Nothing was retrieved; nothing is copied from or attributed to any real website,
standard, datasheet or organisation; the `example://` locators do not resolve;
`retrieved_at` is null; and it carries no material property values. The pack
includes its own notice source saying exactly this. It demonstrates the mechanism
and is not evidence about any product.

Because the pack exists to be quoted, the disclaimer is repeated inside the
snapshot text itself: every snapshot begins `ILLUSTRATIVE PLACEHOLDER - NOT A
RETRIEVED SOURCE.`, so a sentence lifted out of one of them stays identifiable
without the surrounding title or notice source.

## Running it

```bash
compoundx-demo \
  --brief demo-results/roller-skate/source/junior_roller_skate_target_brief.pdf \
  --output-dir runs/external-knowledge-demo \
  --external-sources examples/external-sources/illustrative-polymer-notes.json \
  --model <openai-model-id>
```

Any OpenAI model ID that supports structured outputs works here, and it must be
supplied explicitly — there is no default model, so substitute an ID your account
can actually call (or set `COMPOUNDX_DEMO_MODEL`).

`runs/` is scratch; run directories are never overwritten. The report lists every
snapshot with its SHA-256 under "External source snapshots", and each introduced
claim is quoted with an `(external: ...)` label beside brief-derived evidence.
With no API key, `python -m pytest tests/test_external_knowledge.py` shows the same
behaviour offline against a stub client, including both directions of the
cross-validation guarantee with positive controls.

## What verification does and does not prove

It proves that a cited quote occurs exactly once in the frozen snapshot that was
hashed before the model saw it, that the snapshot stored in the report is the text
that was verified, and that introduced knowledge is reported through a channel a
reader can separate from the brief.

It does not establish that a source is authoritative, current, correctly
transcribed or applicable, nor that the conclusion drawn from the quote is sound.
Whether an external source should be relied on remains human engineering judgement.
