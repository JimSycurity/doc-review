# Contributing

Thanks for helping. Open an issue before a large change so the design can be
agreed first.

## Set up and check

You need [uv](https://docs.astral.sh/uv/) 0.12.19. From the repository root:

```bash
uv sync --frozen
uv run pre-commit install       # optional: run checks on each commit
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pytest
uv run docreview facts --root .
uv run docreview catalog --root . --check
uv build
```

Every pull request must pass these. CI runs the same list.

## Rules

- Keep the engine free of runtime dependencies.
- Tests must never call a provider or read a real credential.
- The declaration and report formats are public. A change to either needs a
  version bump, a [CHANGELOG.md](CHANGELOG.md) entry and updated docs.
- Docs under `docs/` use only `type` and `status` frontmatter. Regenerate
  `docs/README.md` with `uv run docreview catalog --root .`.
- Write docs in plain English with ASCII characters only.
