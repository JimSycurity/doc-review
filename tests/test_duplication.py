"""Check lexical retrieval and cache behavior without model calls."""

from pathlib import Path

from docreview.configuration import load_config
from docreview.duplication import load_index


def test_near_duplicate_sections_keep_both_locations(tmp_path: Path) -> None:
    common = " ".join(f"operation{index}" for index in range(120))
    corpus = {
        "docs/usage/recover.md": "# Recover\n\n" + common + "\n",
        "docs/usage/retry.md": "# Retry\n\n"
        + common
        + " additional recovery advice for users.\n",
        "docs/usage/install.md": "# Install\n\n"
        + " ".join(f"install{index}" for index in range(120))
        + "\n",
    }
    index = load_index(load_config(tmp_path), corpus)
    pairs = index.candidates({"docs/usage/recover.md"}, 0.5)
    assert len(pairs) == 1
    assert pairs[0].left.path == "docs/usage/recover.md"
    assert pairs[0].right.path == "docs/usage/retry.md"
    assert pairs[0].left.line_start == 1
    assert pairs[0].right.heading == "Retry"
    assert pairs[0].estimated_overlap >= 0.5


def test_owned_cache_is_reused_and_invalidated_by_content(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    config = load_config(tmp_path)
    corpus = {"README.md": "# Package\n\n" + " ".join(f"word{i}" for i in range(40))}
    first = load_index(config, corpus)
    assert not first.cache_hit
    second = load_index(config, dict(corpus))
    assert second.cache_hit
    assert first.records == second.records
    assert list((tmp_path / ".tmp/docreview/duplicates").glob("*.json"))
    changed = load_index(
        config,
        {**corpus, "CONTEXT.md": "# Terms\n\nNew vocabulary for the range model."},
    )
    assert not changed.cache_hit
    assert changed.tree_sha256 != first.tree_sha256
    assert load_index(config, corpus).cache_hit


def test_facts_report_exposes_candidates_without_claiming_a_defect(
    tmp_path: Path,
) -> None:
    import io
    import json
    from contextlib import redirect_stdout

    from docreview.cli import main

    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    folder = tmp_path / "docs/usage"
    folder.mkdir(parents=True)
    common = " ".join(f"operation{i}" for i in range(120))
    for name in ("recover.md", "retry.md"):
        (folder / name).write_text("---\ntype: runbook\n---\n# Operation\n\n" + common)
    with redirect_stdout(io.StringIO()) as output:
        assert main(["facts", "--root", str(tmp_path), "docs/usage/recover.md"]) == 0
    report = json.loads(output.getvalue())
    row = report["documents"][0]
    assert row["route"] == "review"
    assert not row["hard_failures"]
    assert len(row["facts"]["duplicate_candidates"]) == 1
    assert report["duplication_index"]["sections_indexed"] == 2
    assert not report["duplication_index"]["cache_hit"]
    assert (
        "duplication: retrieval index is not implemented in this slice"
        not in row["not_assessed"]
    )


def test_contributed_mode_never_creates_a_cache(tmp_path: Path) -> None:
    corpus = {
        "README.md": "# Package\n\nDescribe how to operate and recover the service."
    }
    first = load_index(load_config(tmp_path), corpus)
    second = load_index(load_config(tmp_path), corpus)
    assert first.records == second.records
    assert not second.cache_hit
    assert not (tmp_path / ".tmp").exists()


def test_corrupt_cache_is_rebuilt_instead_of_hiding_candidates(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    corpus = {
        "README.md": "# Package\n\nDescribe how to operate and recover the service."
    }
    config = load_config(tmp_path)
    original = load_index(config, corpus)
    cache = next((tmp_path / ".tmp/docreview/duplicates").glob("*.json"))
    cache.write_text("{truncated")
    repaired = load_index(config, corpus)
    assert not repaired.cache_hit
    assert repaired.cache_error
    assert repaired.records == original.records
    assert load_index(config, corpus).cache_hit


def test_exclusions_and_non_markdown_inputs_do_not_affect_the_index(
    tmp_path: Path,
) -> None:
    config = load_config(tmp_path)
    document = "# Guide\n\nExplain how to operate and recover the service safely."
    first = load_index(config, {"README.md": document})
    second = load_index(
        config,
        {
            "README.md": document,
            "docs/fixtures/copy.md": document,
            "source.py": document,
        },
    )
    assert first.tree_sha256 == second.tree_sha256
    assert all(section.path == "README.md" for section in second.records)
    assert not second.candidates({"README.md"}, 0.5)


def test_same_document_duplicates_and_short_sections_are_visible(
    tmp_path: Path,
) -> None:
    content = (
        "Perform recovery only after saving a current copy of the worker configuration."
    )
    corpus = {
        "README.md": "# Guide\n\n## First\n\n"
        + content
        + "\n\n## Second\n\n"
        + content
        + "\n"
    }
    index = load_index(load_config(tmp_path), corpus)
    assert index.skipped_sections == 1
    pairs = index.candidates({"README.md"}, 0.5)
    assert len(pairs) == 1
    assert pairs[0].left.line_start != pairs[0].right.line_start


def test_threshold_and_input_order_are_deterministic(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    content = " ".join(f"word{i}" for i in range(100))
    corpus = {
        "README.md": "# Guide\n\n" + content,
        "CONTEXT.md": "# Terms\n\n" + content + " More vocabulary words.",
    }
    first = load_index(config, corpus)
    second = load_index(config, dict(reversed(list(corpus.items()))))
    assert first.tree_sha256 == second.tree_sha256
    assert first.candidates(set(corpus), 0.5) == second.candidates(set(corpus), 0.5)
    assert first.candidates(set(corpus), 0.5)
    assert not first.candidates(set(corpus), 1)


def test_globaltech_sized_fixture_reuses_the_index_within_three_seconds(
    tmp_path: Path,
) -> None:
    import random
    from time import perf_counter

    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    folder = tmp_path / "docs/reference"
    folder.mkdir(parents=True)
    generator = random.Random(20261018)
    vocabulary = [f"w{i:06d}" for i in range(32768)]
    bodies = [
        [generator.choices(vocabulary, k=205) for _ in range(7)] for _ in range(376)
    ]
    bodies[-1][0] = [*bodies[0][3][:-5], *generator.choices(vocabulary, k=5)]
    corpus = {}
    for number, content in enumerate(bodies):
        path = f"docs/reference/platform-{number:04d}.md"
        text = (
            f"---\ntype: reference\n---\n# Platform {number}\n\n"
            + "\n\n".join(
                f"## Section {index}\n\n" + " ".join(words)
                for index, words in enumerate(content)
            )
            + "\n"
        )
        corpus[path] = text
        (tmp_path / path).write_text(text)
    config = load_config(tmp_path)
    started = perf_counter()
    cold = load_index(config, corpus)
    cold_seconds = perf_counter() - started
    started = perf_counter()
    warm = load_index(config, corpus)
    pairs = warm.candidates(
        {"docs/reference/platform-0000.md"}, config.limits.duplicate_overlap
    )
    warm_seconds = perf_counter() - started
    assert warm.cache_hit
    assert warm.records == cold.records
    assert any(pair.right.path == "docs/reference/platform-0375.md" for pair in pairs)
    assert warm_seconds < 3
    print(
        f"Duplication fixture: {len(corpus)} files, {sum(len(text.encode()) for text in corpus.values())} bytes, {len(warm.records)} indexed sections; cold={cold_seconds:.3f}s, warm retrieval={warm_seconds:.3f}s"
    )


def test_unavailable_cache_does_not_escape_the_repository(tmp_path: Path) -> None:
    from tempfile import TemporaryDirectory

    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    corpus = {
        "README.md": "# Package\n\nDescribe how to operate and recover the service."
    }
    with TemporaryDirectory() as directory:
        outside = Path(directory)
        (tmp_path / ".tmp").symlink_to(outside, target_is_directory=True)
        index = load_index(load_config(tmp_path), corpus)
        assert index.records
        assert index.cache_error
        assert not list(outside.iterdir())
