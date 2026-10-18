---
type: runbook
---

# Deterministic checks

`docreview facts` and `docreview catalog` check documentation with code only.
They never read a credential or contact a model. Use them in CI, in a
pre-commit hook and before asking for a model review. This page explains how
to run them and every rule they apply.

## Run the checks

```bash
# Check every maintained page and print the JSON report.
uv run docreview facts --root /path/to/repo

# Check selected pages; the whole corpus is still read for incoming links.
uv run docreview facts --root /path/to/repo docs/usage/recover.md

# Tell the engine which selected pages are new or moved.
uv run docreview facts --root /path/to/repo --new docs/usage/recover.md \
  docs/usage/recover.md
uv run docreview facts --root /path/to/repo --moved docs/design/proposal.md \
  docs/design/proposal.md

# Generate directory catalogs (owned mode), or check them without writing.
uv run docreview catalog --root /path/to/repo
uv run docreview catalog --root /path/to/repo --check
```

`--new` and `--moved` paths must also be selected. Put all options before
the page paths; options placed between page paths are rejected. The engine does not
compare Git revisions; the caller decides which pages are new or moved. In
`owned` mode, `--out report.json` writes the report relative to the root.
Stdout is the default. See [report](output.md) for the output and exit codes.

## What a hard failure means

A hard failure is a located rule violation. It routes the page to `revise`
for the author, and it is never sent to a model. A page with no hard failure
still routes to `review`: facts alone cannot show that content is good.

## Which pages are checked

The maintained corpus is every `.md` file that matches `scope.maintained` and
not `scope.excluded`. The walk skips `.git`, `.venv`, `node_modules` and
`__pycache__` and does not follow symbolic links out of the repository.

## Frontmatter contract

Frontmatter is optional, closed and tiny. It has at most two keys, each a
plain `key: value` line between `---` lines at the top of the file.

| Key | Required | Value |
| --- | --- | --- |
| `type` | every maintained page under `docs/` that is not fixed-name or generated | a configured type |
| `status` | only for a type with a status list | a value from that type's list |

Values are unquoted lowercase words: a letter, then letters, digits, `_` or
`-`. Lists, nesting, anchors, quotes and multi-line values are rejected.

Rejected keys get a reason so an agent does not add them back:

| Keys | Reason given |
| --- | --- |
| `title` | the first heading owns the title |
| `description`, `summary`, `read_when`, `purpose` | the governing index owns the one-line routing text |
| `date`, `created`, `updated`, `last_reviewed` | Git history owns dates |
| `author`, `owner`, `maintainer` | Git and code ownership own attribution |
| `tags`, `keywords`, `category`, `topics` | no consumer reads them; `type` is the only category |
| `version` | nothing increments it |
| `related`, `links`, `see_also` | body links own relationships |
| `id`, `slug`, `uid` | the path is the identifier |
| `draft`, `wip`, `archived`, `deprecated` | `status` owns lifecycle |
| any other key | not in the contract |

Fixed-name files (`fixed_types`) and generated catalogs carry no frontmatter.
Skill and agent runtime files keep their own schema and are skipped:
any `SKILL.md`, and files under `.agents/`, `.claude/` or `.codex/` in an
`agents/` or `commands/` directory.

## Type resolution and placement

The engine resolves a page's type in this order:

1. A fixed filename, matched on the full path or the file name.
2. The frontmatter `type`.
3. The one type whose `paths` match. When several match, or none does, the
   type stays unknown. Review can then ask the model to classify it.

Placement fails when a page's type does not allow its path. On a `--moved`
page this goes to review instead, because a move is an ownership decision.

## Rules

| Rule | Fails when |
| --- | --- |
| `frontmatter.unknown_key` | a key other than `type` or `status` appears |
| `frontmatter.scalar` | a line is not a plain scalar or the value is not a lowercase word |
| `frontmatter.duplicate_key` | a key appears twice |
| `frontmatter.unclosed` | the closing `---` is missing |
| `frontmatter.forbidden` | a fixed-name or generated file has frontmatter |
| `frontmatter.type_missing` | a page under `docs/` needs `type` and has none |
| `frontmatter.type_unknown` | `type` names a type the declaration does not have |
| `frontmatter.status_missing` | a lifecycle type has no `status` |
| `frontmatter.status_invalid` | `status` is outside the type's list, or the type has no lifecycle |
| `frontmatter.inline_status` | frontmatter has `status` and the body repeats a `Status:` label |
| `placement.type_path` | the type does not allow this path |
| `links.unresolved` | a relative link or `#anchor` does not resolve, leaves the repository, or uses an undefined reference label |
| `navigation.redirect_chain` | following moved stubs exceeds `limits.redirect_chain` |
| `navigation.redirect_cycle` | moved stubs form a loop |
| `navigation.orphan` | a `--new` page of 100 words or more has no acceptable inbound link |
| `terms.banned` | a retired word appears |

Links to web URLs are not fetched. A redirect is a page whose prose starts
with "Moved" and has one link, or a page that is only one link.

For `navigation.orphan`, types listed in `curated_required_for` need a link
from a `curated` page. Other types also accept a parent: any `README.md` or
`CONTEXT-MAP.md`, or the root `AGENTS.md`.

## Facts that never fail

The report also records, per page: bytes, words, the heading tree with line
ranges, link counts and sources, `placement_ok`, concept term usage,
execution signals, readability and duplicate candidates.

- **Execution signals** flag sections with timestamps, exit codes, the word
  "receipt", UUIDs or console transcript fences. Review asks whether such a
  section belongs in stable guidance.
- **Readability** is words per sentence and the share of words longer than ten
  letters, per section. It travels with the plain English question as a hint.
- **Duplicate candidates** are pairs of heading sections with similar text.

## Duplicate candidates

The engine compares groups of five words between heading sections across the
whole maintained corpus. Pairs at or above `limits.duplicate_overlap` become
candidates with both locations. Similarity alone never fails a page or shows
which page owns the guidance. Sections shorter than five words are skipped and
counted in the report's `duplication_index`. Reworded duplication is not
detected, and the report says so.

In `owned` mode the index is cached under
`.tmp/docreview/duplicates/<tree-hash>.json`. The key covers paths, content,
section offsets and the algorithm version. A corrupt or unreadable cache is
rebuilt in memory and reported. `contributed` mode never writes a cache. Keep
`.tmp/` out of Git.

## Directory catalogs

`catalog` builds a `README.md` for each directory that matches
`scope.generated`. Each row comes from a page's type, status, first heading
and first paragraph. Catalogs link child catalogs, skip excluded pages and do
not change between runs.

- A configured `curated` index stays hand-written. If it is missing, that is a
  failure.
- An existing `README.md` without the generated marker line is an ownership
  conflict. The engine reports it and writes nothing.
- `--check` exits `1` in `owned` mode when a catalog is stale.
- `contributed` mode prints the proposed catalog and exits `0`.

## Not yet available

`docreview adopt --report-only` is reserved for a later adoption report. It
currently exits `2` with a "not implemented" message.
