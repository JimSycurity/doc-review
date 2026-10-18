# docreview

Inspect maintained documentation and return located findings to its author or
reviewer. Checks do not edit documents, dispatch agents or approve changes.
Python 3.12 or later is required; the runtime uses only the standard library.

From this package directory:

```bash
uv sync
uv run docreview --help
uv run docreview facts --root /path/to/repo docs/usage/recover.md
uv run docreview review             # request preview; no network or credential use
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv build
```

## Deterministic checks

`facts` reads the selected pages and uses the whole maintained Markdown corpus
for incoming links and redirects. It reads the repository's `docs-review.toml`;
the [declaration contract](../../docs/design/documentation-review-boundary.md#project-declaration-file)
owns its fields. Without a declaration, checks use the default types and remain
in read-only `contributed` mode. They never send content to a provider.

```bash
# Check every maintained page and print the JSON report.
uv run docreview facts --root /path/to/repo

# New pages of at least 100 words must have an index or parent link.
# Runbooks, context and agent instructions need a configured curated route.
uv run docreview facts --root /path/to/repo --new docs/usage/recover.md \
  docs/usage/recover.md

# A moved page's type/path conflict goes to ownership review.
uv run docreview facts --root /path/to/repo --moved docs/design/proposal.md \
  docs/design/proposal.md
```

Reports include declaration, placement, section ranges, incoming and outgoing
links, redirects, retired words, concept-scoped term usage, execution signals
and readability facts. Only retired words (the inline list and `global` term
sources) fail a document. A `concept` source such as a glossary avoid list
records each match with its concept for the model's term usage question.
Execution, readability and term usage signals alone never fail a document. A page with no
hard failure still routes to review: these facts do not establish content quality.
The caller supplies new/moved paths; the engine does not infer a Git comparison.

Exit `0` means completed, `1` means an owned repository has enforcing failures,
and `2` means incomplete. Contributed mode reports violations with exit `0` and
refuses file writes. In owned mode, `--out report.json` writes the report relative
to the repository root; stdout is the default. Unknown declaration keys and
unsafe or unavailable sources prevent completion.

## Directory catalogs

```bash
uv run docreview catalog --root /path/to/repo          # generate in owned mode
uv run docreview catalog --root /path/to/repo --check  # check without writing
```

Catalogs use each page's type, status, first heading and first purpose paragraph.
They include links to child directories, omit excluded pages and remain stable
across repeated runs. `--check` returns `1` for stale output in owned mode.
Configured curated indexes stay hand-written. An existing unmarked README is an
ownership conflict; generation reports it and writes nothing. Missing curated
indexes and invalid source declarations also prevent generation. Contributed
mode always prints a proposed catalog and returns `0` for findings.

## Duplication candidates

`facts` compares groups of five words in heading sections and reports similar
section pairs with both file locations. Similarity alone never fails a page or
establishes that either page owns the guidance. Sections shorter than five words
have no such groups; the index metadata counts them as skipped. The report
keeps model review and possible paraphrased duplication explicitly unassessed.

Owned mode stores a disposable index under
`.tmp/docreview/duplicates/<tree-hash>.json`. The key includes the maintained
Markdown paths, content, section offsets and algorithm version; changed, added
or removed pages select a new cache. Corrupt or unavailable caches are rebuilt
in memory and reported. Contributed mode never writes a cache. Keep `.tmp/`
out of Git; no index or generated test corpus belongs in a commit.

## Model-assisted review

```bash
uv run docreview review --root /path/to/repo docs/usage/recover.md
uv run docreview review --cases /private/documents.json
uv run docreview review --docstrings /private/changed-functions.json
```

Review joins the same facts with self-contained questions for complete sections,
within the serialized request byte limit. It returns located `pass`, `revise` or
`review` handoffs, with separate action bands, repeated boundary samples and
original answer distributions. Oversized inputs and missing context stay review
gaps. Dry-run previews never count as model answers. Live calls require both
`--live` and `--key-file`; no environment credential fallback is used.

The [review guide](../../skills/code-review-and-qa/documentation-review/references/engine.md)
covers snapshot and docstring schemas, context completeness, repair history,
evaluation labels and report interpretation. Model criteria remain advisory
unless a project explicitly promotes them. Tests use controlled responses;
model quality and the starting bands still need calibration.

The [boundary design](../../docs/design/documentation-review-boundary.md)
specifies later skill/profile integration. `adopt --report-only` reserves the
later adoption command and currently returns an explicit unavailable error.
