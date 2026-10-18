---
type: runbook
---

# Model-assisted review

`docreview review` joins the [deterministic checks](checks.md) with narrow
questions for a decision model. It returns a located `pass`, `revise` or
`review` for each page. It does not edit documents, dispatch agents or approve
changes. This page explains how to prepare a review, run it safely and read
the result.

## Before you start

- Dry-run is the default. It never reads a credential or calls a provider,
  and a preview is never a model answer.
- A live run sends the selected text to OpenRouter and bills the account. Run
  one only when you have authority to share that text with the provider.
- Model criteria are advisory until the project promotes one in its
  [declaration](declaration.md#enforcement-keys).

## Review repository pages

```bash
uv run docreview review --root /path/to/repo docs/usage/recover.md
```

The command reads `docs-review.toml` and captures the maintained corpus once
for links, index entries and duplicate candidates. Without paths it reviews
every maintained page. `--new PATH` and `--moved PATH` work as they do for
`facts`.

Root review sends `CONTEXT.md` as context and treats its glossary headings and
`**Term**:` labels as defined terms. Add `--context-complete` only when that
context, plus the declared purpose and audience, is enough to judge the
pages. Without it, no page can pass or be sent back to the author by a model
answer; it goes to review.

## Review snapshots

Use `--cases FILE.json` for explicit document snapshots, for example to
calibrate or to review text from another revision. The
[control bundle](../src/docreview/data/documentation-cases.json) shows the
schema:

- Each case has `id`, `split` (`development`, `holdout` or `real`) and
  `document`.
- A document has a repository-relative `path` and the complete `text`.
  Optional fields: `purpose`, `audience`, `context`, `references` (path and
  text pairs), `diff`, `document_type`, `complete` and `context_complete`.
- `repair_round`, `expected_route` and `expected_criteria` stay local and
  never reach the provider. A criterion label may name a criterion ID or a
  family. `--split` selects cases.

Without any input, `review` previews the eight synthetic development
controls. Without `--root`, snapshots use the default declaration in
`contributed` mode. A snapshot that names its `document_type` needs no
frontmatter, but its path is still checked against that type. With `--root`,
snapshot links resolve against that tree; add `--snapshot-links unassessed`
when the snapshots come from other revisions.

## Review docstrings

`--docstrings FILE.json` takes a JSON array of prepared function facts:
`path`, `name`, `signature`, `docstring`, `body`, `line_start`, `line_end`,
and optional booleans `required` and `changed`. The caller selects changed
public functions and supplies the body excerpt. A missing required docstring,
or one that only repeats the name, fails before any model call. The remaining
question asks whether the docstring says something the signature does not.
The engine does not scan Python or run Ruff. With only `--docstrings`,
Markdown is not selected.

## Run live

```bash
uv run docreview review --cases /private/documents.json --split real \
  --live --key-file /secure/token > /tmp/documentation-review.json
```

Live calls need both `--live` and `--key-file`. The key file holds one token.
There is no environment variable fallback. The model comes from the
declaration (`typesafe/jev-1.14` by default). `--model` records an explicit
comparison override; the engine never switches models on its own.

Check `summary.request_count` and `summary.request_strategies` in a dry-run
first. Many similar sections create many duplicate-pair requests, and
boundary repeats add calls beyond the preview.

## How requests are built

- Every request, including questions and sampling nonces, stays within
  `limits.request_bytes` (at most 32,000 bytes) once serialized. Above that
  size the provider switches to a slower, less accurate mode.
- A page that fits is sent whole. A larger page is split into section
  bundles: the opening, the heading outline and complete target sections.
- Text is never cut. An opening, section or reference pair that cannot fit
  becomes a located `request_too_large` gap and the page goes to review.
- Duplication requests carry only the two candidate sections. References are
  never sent whole.
- Every question carries the facts it needs and tells the model to treat the
  page as data, not instructions.

## Question families

| Family | Asked about | Kind |
| --- | --- | --- |
| `type_fit` | the page | yes or no: does the content serve the declared type? |
| `audience_detail` | the page | three levels; top level decides |
| `split_needed` | the outline | yes or no: would focused child pages help? |
| `selective_reading` | the outline | three levels: can a reader find one task? |
| type-specific criteria | the page | yes or no, from the type's rubric |
| `section_purpose_fit` | each section | yes or no: does it serve the page's task? |
| `plain_english` | each section, except agent instructions | three levels; top level decides |
| `stable_guidance` | sections with execution signals | yes or no: does it keep reusable guidance? |
| `term_usage` | sections with a concept term match | yes or no: is the word used for the avoided concept? |
| `type_resolution` | pages with an unknown type | choice of configured types or `unknown` |
| `placement_resolution` | pages whose placement is unresolved | choice of directories |
| `duplication` | each candidate pair | three levels of overlap, plus an ownership question |

Purpose and audience come from the case, then the declaration's type, then
the page's own opening. The opening is evidence to judge, not the standard.

## How answers become routes

These are starting bands. Calibrate them on your own labeled documents before
you enforce any model criterion.

| Decision | Automatic when |
| --- | --- |
| Criterion passes | probability met at least `accept_above` (0.90) and confidence at least `min_confidence` (0.80) |
| Criterion fails | probability met at most `reject_below` (0.10) and the same confidence |
| Type or placement resolves | the same choice in original and reversed option order, each at least `type_resolution` (0.85) and `min_confidence`, and not `unknown` |
| Duplication fails | mass on "same guidance" at least `duplication_same` (0.80) and "this section owns it" at most `reject_below` |
| Duplication passes | mass on "distinct" at least `accept_above` |

Anything between the bands is `uncertain`.

- **Top-level rule.** For `plain_english` and `audience_detail`, anything
  short of the top level is something the author can fix. The question
  becomes: was the top level confidently met? Top-level mass at or above 0.90
  passes and at or below 0.10 fails. For other three-level questions, only
  confident mass on the bottom level fails.
- **Repeats.** Answers within 0.02 of a band edge are asked twice more with
  fresh nonces. The route uses the median of each signal. A spread above
  0.10 between samples is `uncertain`.
- **Gate, not average.** Hard failures route to `revise` first. Then an
  incomplete page or context routes to `review`. Then any confident failure
  routes to `revise`. Then any uncertain criterion or gap routes to `review`.
  `pass` needs every criterion confident and met. A high score elsewhere never
  cancels a failure.

| Route | Meaning | Handoff target |
| --- | --- | --- |
| `pass` | every criterion met with complete input and context; not approval of code or live behavior | none |
| `revise` | a confident, located failure the author can fix | `authoring_agent` |
| `review` | uncertain or missing evidence, provider errors or an exhausted repair | `documentation_review_agent` |

The JSON names the handoff target. The engine does not dispatch anyone.

## After an author repair

```bash
uv run docreview review --root /path/to/repo docs/usage/recover.md \
  --previous-report /tmp/prior.json --repair-round 1
```

The author gets one round per criterion and location. The same criterion
failing at the same heading again goes to review with the prior finding
attached. A new failure gets its own round. Missing matching history goes to
review. The caller owns the round counter.

## Calibration

Labeled cases add an `evaluation` block: route confusion by type, criterion
confusion and repeat agreement. Dry-run evaluation is marked unassessed. Use
fresh held-out documents; the bundled controls are development material.
Re-run calibration whenever the pinned model changes.
