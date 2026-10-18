# Agent instructions

This repository is `docreview`, a Python package that checks Markdown
documentation. Read [CONTEXT.md](CONTEXT.md) for vocabulary and
[docs/README.md](docs/README.md) for the engine's contracts and guides.

## Critical rules

- **Public contracts.** The [declaration format](docs/declaration.md) and the
  [report format](docs/output.md) are public interfaces. Change them only with
  a version bump in `pyproject.toml` and `src/docreview/__init__.py`, a
  [CHANGELOG.md](CHANGELOG.md) entry and matching doc updates. Never rename or
  remove a report field. Projects pin exact versions.
- **No runtime dependencies.** The engine uses only the standard library. Add
  a runtime dependency only with owner approval. Dev tools stay pinned in
  `pyproject.toml` and `uv.lock`.
- **Never send content to a provider in tests.** Dry-run is the default. Live
  calls need `--live` and `--key-file`. Tests use controlled responses only.
  Do not add an environment variable credential fallback.
- **Docs follow the engine's own contract.** Pages under `docs/` carry only
  `type` and, for lifecycle types, `status` frontmatter. `docs/README.md` is
  generated; run `uv run docreview catalog --root .` instead of editing it.
- **Code is authoritative.** When a doc and the code disagree, fix the doc or
  raise the disagreement; do not weaken a check to make docs pass.
- **Git.** Work on a branch, use conventional commit subjects (`feat:`,
  `fix:`, `docs:`, `ci:`, `chore:`), and add no attribution trailers. Do not
  push, tag or publish without owner authorization.

## Commands

Run from the repository root. uv 0.12.19 is required (`tool.uv.required-version`).

| Task | Command |
| --- | --- |
| Install | `uv sync --frozen` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Type check | `uv run pyrefly check` |
| Test | `uv run pytest` |
| Build | `uv build` |
| Check own docs | `uv run docreview facts --root .` |
| Check docs catalog | `uv run docreview catalog --root . --check` |
| Pre-commit | `uv run pre-commit run --all-files` |

CI runs the same commands in [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Layout

| Path | Contents |
| --- | --- |
| `src/docreview/` | engine package; `cli.py` is the entry point |
| `src/docreview/data/` | bundled synthetic control cases |
| `tests/` | pytest suite |
| `docs/` | contracts and guides; `docs-review.toml` declares them |
| `.tmp/` | disposable caches; never commit |
