# Changelog

All notable changes to this package are listed here. Versions follow
[Semantic Versioning](https://semver.org/). The declaration and report formats
are public interfaces, so any change to them is listed.

## 0.1.0

First release from this repository.

- Moved the engine, with its Git history, from the owner's private skillz
  repository (`packages/docreview`, tag `docreview-v0.1.0`) so that public
  projects and CI can install it. Code and behavior are unchanged.
- Moved the declaration, report and review contracts from the skillz design
  document into [docs/](docs/README.md).
- Added package metadata, CI, a dogfood `docs-review.toml` and contribution
  and security notes.
