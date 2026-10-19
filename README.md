# docreview

docreview checks Markdown documentation and returns located findings to the
author or reviewer. Code checks paths, links, frontmatter, retired words and
similar text. An optional decision model answers narrow questions about
purpose, audience and clarity. The engine never edits documents, dispatches
agents or approves changes.

It needs Python 3.12 or later and has no runtime dependencies.

## Install

Pin the exact version. A project's declaration must name the same version.

```bash
uv tool install git+https://github.com/JimSycurity/doc-review@v0.1.0
# or, as a project dependency:
uv add "docreview @ git+https://github.com/JimSycurity/doc-review@v0.1.0"
```

For development builds, pin a full commit hash instead of a branch name.

## Quick start

1. Add a `docs-review.toml` at your repository root. Start with
   `authority = "contributed"` (report only) or `"owned"` if you own the
   project. See [project declaration](docs/declaration.md).
2. Run the deterministic checks. They never contact a model.

   ```bash
   docreview facts --root .
   ```

3. Fix each hard failure in the JSON report. In `owned` mode the command exits
   `1` while any remain.
4. Optionally preview a model review. Dry-run is the default and sends
   nothing.

   ```bash
   docreview review --root . docs/usage/recover.md
   ```

## Documentation

The [documentation index](docs/README.md) lists every guide:
[deterministic checks](docs/checks.md),
[model-assisted review](docs/review.md),
[project declaration](docs/declaration.md) and
[report and exit codes](docs/output.md).

## Develop

See [CONTRIBUTING.md](CONTRIBUTING.md) for commands and rules. Agents start at
[AGENTS.md](AGENTS.md). [SECURITY.md](SECURITY.md) explains what the engine
sends to a provider. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
Licensed under the [MIT license](LICENSE).
