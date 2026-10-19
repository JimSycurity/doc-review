---
type: reference
---

# Project declaration

A project tells the engine about its documents in one file,
`docs-review.toml`, at the repository root. This page defines that file, the
default document types and the two authority modes. The declaration and the
[report](output.md) are the only contract between the engine and a project.

## Without a declaration

If the file is missing, the engine uses the defaults on this page and runs in
`contributed` mode. It reports problems but writes nothing and never exits
with `1`. Add a declaration with `authority = "owned"` before you let the
engine fail CI.

## Authority modes

| Mode | Who uses it | What the engine may do |
| --- | --- | --- |
| `owned` | The project's owners adopted the engine | Exit `1` on enforcing failures, write `--out` reports, write catalogs and the duplication cache under `.tmp/` |
| `contributed` | You are proposing a change to someone else's project | Report only. It refuses `--out`, never writes catalogs or caches, exits only `0` or `2`, and treats every model criterion as advisory |

`contributed` is the default. Choose `owned` only for a repository whose
owners decided to adopt the engine.

## Default document types

`type` in frontmatter takes one of these values. A project can change paths,
purpose, audience and status for any of them, and can add its own types.

| Type | Default paths | Allowed `status` values |
| --- | --- | --- |
| `readme` | fixed filename `README.md` | none |
| `agent_instructions` | fixed filenames `AGENTS.md`, `CLAUDE.md` | none |
| `context` | fixed filename `CONTEXT.md`; `docs/context/**` | none |
| `index` | fixed filename `CONTEXT-MAP.md`; `docs/indexes/**` | none |
| `runbook` | `docs/usage/**` | none |
| `reference` | `docs/reference/**` | none |
| `design` | `docs/design/**` | `draft`, `proposed`, `accepted`, `superseded`, `rejected` |
| `plan` | `docs/plans/**` | `draft`, `active`, `completed`, `superseded` |
| `adr` | `docs/adr/**`, `docs/decisions/**` | `proposed`, `accepted`, `superseded`, `rejected` |
| `policy` | `docs/policies/**` | none |
| `analysis` | `docs/analysis/**` | none |
| `report` | `docs/reports/**` | none |

A type with a status list requires `status`. A type without one forbids it.
`runbook` uses the engine's internal rubric name `human_instructions`, and
snapshot inputs may still use that older name.

Issues, skill and persona entry points, and generated catalogs are not
document types.

## File format

Unknown sections and unknown keys are errors. Every path list uses
gitignore-style globs relative to the root: `**` spans directories, `*` stays
inside one, a leading `!` re-includes or excludes, and a pattern without `/`
matches any path segment. Paths must stay inside the repository: no leading
`/`, no `..` and no backslashes.

```toml
[review]
standard = "project"          # or "skillz-default"
standard_version = 1          # optional; only 1 is accepted
authority = "owned"           # or "contributed" (the default)
engine = "docreview==0.1.0"   # must equal the running version exactly

[scope]
maintained = ["README.md", "AGENTS.md", "CLAUDE.md", "CONTEXT.md", "CONTEXT-MAP.md",
              "docs/**/*.md", ".agents/**/*.md", ".claude/**/*.md"]
excluded  = [".archive/**", ".tmp/**", "**/fixtures/**", "docs/archive/**"]
generated = ["docs/**/README.md"]   # catalog targets; exempt from frontmatter
fixed_types = { "README.md" = "readme", "AGENTS.md" = "agent_instructions", "CLAUDE.md" = "agent_instructions", "CONTEXT.md" = "context", "CONTEXT-MAP.md" = "index" }

[types.runbook]               # change a default type
paths = ["docs/usage/**"]
purpose = "Tell people how to perform and recover an operation"
audience = "operators who can use a shell but do not develop the system"
operator_facing = true        # recorded for project tooling; the engine ignores it

[types.narrative]             # add a project type
paths = ["docs/history/**"]
purpose = "Explain the people and relationships behind the product"
audience = "new readers learning the project"
rubric = "common"

[indexes]
curated = ["CONTEXT-MAP.md", "AGENTS.md", "docs/history/README.md"]
curated_required_for = ["agent_instructions", "runbook", "context"]

[terms]
inline  = ["oldname"]         # retired everywhere: hard failures
sources = [{ file = "CONTEXT.md", pattern = "^_Avoid_: (.*)$", split = ", ", scope = "concept", concept_pattern = "^\\*\\*([^*]+)\\*\\*:" }]

[limits]
request_bytes = 32000         # largest serialized model request
redirect_chain = 2
duplicate_overlap = 0.5       # estimated overlap that makes a candidate pair

[model]
provider = "openrouter"
id = "typesafe/jev-1.14"      # pinned; calibration is void when it changes

[thresholds]
accept_above = 0.90
reject_below = 0.10
min_confidence = 0.80
type_resolution = 0.85
duplication_same = 0.80

[enforcement]
plain_english = { level = "advisory", issue = 0 }
```

The values shown under `scope`, `indexes`, `limits`, `model` and `thresholds`
are the defaults. `standard` defaults to `skillz-default`. It is recorded and
validated but does not change engine behavior.

## Loader rules

The loader stops the run with exit `2` when any of these fail:

- `standard` is `skillz-default` or `project`; `authority` is `owned` or
  `contributed`.
- `engine`, when present, equals `docreview==` plus the running version.
  Version ranges are not accepted.
- A type table uses only `paths`, `status`, `purpose`, `audience`, `rubric`
  and `operator_facing`. `rubric` names a default type, `human_instructions`
  or `common`. A `common` type gets the shared questions and no
  type-specific ones. A new type defaults to `common`.
- `fixed_types` and `curated_required_for` name known types.
- A term source has a `pattern` with exactly one capture group and a
  nonempty `split`. `scope` is `concept` (the default) or `global`. An
  optional `concept_pattern` needs exactly one capture group and is allowed
  only for `concept` sources.
- `request_bytes` is from 1 through 32000. `redirect_chain` is a nonnegative
  integer. `duplicate_overlap` and every threshold is a fraction from 0
  through 1.
- `reject_below` is below 0.5 and `accept_above` is above 0.5.
  `type_resolution` and `duplication_same` are above 0.5.
- `model.provider` is `openrouter` and `model.id` is not empty.
- An `enforcement` entry has `level` `advisory` or `enforcing`. `enforcing`
  needs a nonzero `issue`: the work item that promoted the criterion. In
  `contributed` mode every level becomes `advisory`.

A type glob that matches no maintained file adds a line to the report's
`warnings` list. This includes default types the project does not use.

## Term sources

- **Inline terms and `global` sources** list retired words. Each use is a
  hard failure.
- **`concept` sources** list words to avoid for one glossary concept. The
  nearest line above that matches `concept_pattern` names the concept. A
  match is a fact for the model's term usage question, never a failure by
  itself, because the word may be correct for another meaning.

Matching ignores case and whole words only. Code, quoted text, block quotes
and the defining entry itself do not count. An avoid entry that wraps onto
following lines is joined until a blank line or the next block before it is
split.

## Enforcement keys

Keys under `enforcement` are question family names: `type_fit`,
`audience_detail`, `split_needed`, `selective_reading`,
`section_purpose_fit`, `plain_english`, `stable_guidance`, `term_usage`,
`type_resolution`, `placement_resolution`, `duplication` and the type-specific
criteria. Deterministic facts do not need an entry. In `owned` mode they
always enforce.
