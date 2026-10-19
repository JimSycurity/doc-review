# Context

Stable vocabulary for the engine. Code names follow these terms.

**Declaration**: the project's `docs-review.toml`. It sets document types,
scope, terms, limits, the pinned model, thresholds and enforcement. See
[docs/declaration.md](docs/declaration.md).

**Authority mode**: `owned` lets the engine fail CI and write files;
`contributed` only reports. Without a declaration the mode is `contributed`.

**Maintained corpus**: every Markdown file the declaration's scope includes.
Links, incoming routes and duplicates are computed across all of it.

**Facts**: what code can establish about a page, such as type, placement,
links, sections and similar text. Facts never involve a model.

**Hard failure**: a located fact that breaks a rule. It routes the page to the
author and is never sent to a model.

**Criterion**: one question about one location, such as whether a section is
plain English. Each criterion is `pass`, `fail` or `uncertain`.

**Family**: the kind of question a criterion belongs to, such as
`plain_english`. Enforcement is set per family.

**Route**: the result for a page: `pass`, `revise` (back to the author) or
`review` (to a documentation reviewer).

**Handoff**: the part of the report that names the route's target and the
located items it must act on. The engine never dispatches it.

**Case**: one document snapshot supplied as explicit JSON input, with optional
local labels.

**Snapshot**: the complete text of a document at one revision, as sent in a
case.

**Calibration**: comparing routes against owner-labeled cases to choose
thresholds. Bands are starting points until a project calibrates them.
