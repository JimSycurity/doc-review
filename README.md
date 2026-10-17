# docreview

Inspect maintained documentation and return located findings to its author or
reviewer. Checks do not edit documents, dispatch agents or approve changes.
Python 3.12 or later is required; the runtime uses only the standard library.

From this package directory:

```bash
uv sync
uv run docreview --help
uv run docreview facts --root /path/to/repo docs/usage/recover.md
uv run docreview review             # moved trial preview; no network or credential use
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
links, redirects, banned terms, execution signals and readability facts.
Execution and readability signals alone never fail a document. A page with no
hard failure still routes to review: these facts do not establish content quality.
The caller supplies new/moved paths; the engine does not infer a Git comparison.

Exit `0` means completed, `1` means an owned repository has enforcing failures,
and `2` means incomplete. Contributed mode reports violations with exit `0` and
refuses file writes. In owned mode, `--out report.json` writes the report relative
to the repository root; stdout is the default. Unknown declaration keys and
unsafe or unavailable sources prevent completion.

## Trial and planned commands

The [review trial guide](../../skills/code-review-and-qa/code-review/references/documentation-decision-trial.md)
explains the moved whole-document trial's bundle and advisory output. Its
questions and report remain unchanged while the new deterministic layer is
built. It is separate from `facts`; this slice does not yet join them.

The [review boundary design](../../docs/design/documentation-review-boundary.md)
specifies catalog generation, duplication retrieval and the section-based model
rewrite. Reports name those unassessed areas. `catalog` and `adopt --report-only`
reserve later commands and currently return an explicit unavailable error.
