"""Explicit repository and snapshot inputs for the advisory review command."""

from __future__ import annotations

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from docreview.configuration import default_config, load_config
from docreview.docstrings import FunctionDoc, parse_function
from docreview.documentation_decisions import Json, as_object, nonempty_text, read_json
from docreview.documentation_inputs import Case, Document, load_cases
from docreview.facts import read_corpus, report_exit_code
from docreview.paths import repository_path
from docreview.review_engine import review_cases

if TYPE_CHECKING:
    import argparse

    from docreview.configuration import ProjectConfig


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "paths",
        nargs="*",
        help="Selected maintained repository-relative Markdown pages",
    )
    parser.add_argument("--root", type=Path, help="Repository with docs-review.toml")
    parser.add_argument(
        "--cases",
        type=Path,
        help="Explicit JSON document snapshots and local evaluation labels",
    )
    parser.add_argument(
        "--split", choices=["all", "development", "holdout", "real"], default="all"
    )
    parser.add_argument(
        "--docstrings",
        type=Path,
        help="Prepared changed public-function facts in a JSON array",
    )
    parser.add_argument(
        "--previous-report",
        type=Path,
        help="Prior report for repair history and repeat agreement",
    )
    parser.add_argument(
        "--repair-round",
        type=int,
        default=None,
        help="Caller-owned author repair counter",
    )
    parser.add_argument(
        "--context-complete",
        action="store_true",
        help="Caller confirms repository review context is complete",
    )
    parser.add_argument("--new", action="append", default=[], metavar="PATH")
    parser.add_argument("--moved", action="append", default=[], metavar="PATH")
    parser.add_argument(
        "--model", help="Explicit comparison override of the configured pinned model"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="Allow provider calls; also requires --key-file",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only (default); never read credentials",
    )
    parser.add_argument(
        "--key-file", type=Path, help="Single API token; read only with --live"
    )
    parser.add_argument(
        "--out", help="Write JSON inside an owned repository; stdout by default"
    )


def _functions(path: Path | None) -> tuple[FunctionDoc, ...]:
    if path is None:
        return ()
    value = read_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise TypeError("docstrings input must be a JSON array")
    functions = tuple(parse_function(item) for item in value)
    if len({(item.path, item.name) for item in functions}) != len(functions):
        raise ValueError("function identities must be unique within a file")
    return functions


def _inputs(
    args: argparse.Namespace, config: ProjectConfig
) -> tuple[list[Case], dict[str, str] | None]:
    if args.cases:
        if args.paths or args.new or args.moved:
            raise ValueError(
                "--cases cannot be combined with repository paths or new/moved flags"
            )
        return load_cases(args.cases, args.split, set(config.types)), None
    if args.root and (args.paths or not args.docstrings):
        corpus = read_corpus(config)
        paths = args.paths or sorted(corpus)
        if set(paths) - corpus.keys():
            raise ValueError("selected paths must belong to the maintained corpus")
        context = corpus.get("CONTEXT.md", "")
        cases = [
            Case(
                path,
                "real",
                Document(
                    path,
                    corpus[path],
                    context=context,
                    context_complete=args.context_complete,
                ),
            )
            for path in paths
        ]
        return cases, corpus
    if args.paths or args.new or args.moved:
        raise ValueError("repository paths and new/moved flags require --root")
    if args.docstrings:
        return [], None
    resource = files("docreview").joinpath("data/documentation-cases.json")
    # Installed wheel resources have a real filesystem path with this build backend.
    return load_cases(Path(str(resource)), args.split, set(config.types)), None


def _key(args: argparse.Namespace) -> str | None:
    if not args.live:
        return None
    if args.key_file is None:
        raise ValueError("--live requires --key-file; environment fallback is disabled")
    path = args.key_file
    if not isinstance(path, Path):
        raise TypeError("credential source must be a path")
    value = path.read_text(encoding="utf-8").strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("credential file must contain one nonempty token")
    return value


def _exit_code(config: ProjectConfig, report: dict[str, Json]) -> int:
    if as_object(report["summary"], "summary")["model_failures"]:
        return 2
    if report_exit_code(config, report):
        return 1
    if config.authority != "owned":
        return 0
    values = report["documents"]
    if not isinstance(values, list):
        raise TypeError("documents must be a list")
    return int(any(_enforcing(as_object(value, "document")) for value in values))


def _enforcing(row: dict[str, Json]) -> bool:
    return any(
        criterion.get("status") == "fail"
        and criterion.get("enforcement") == "enforcing"
        for value in as_object(row["criteria"], "criteria").values()
        if isinstance((criterion := value), dict)
    )


def run_review(args: argparse.Namespace) -> int:
    config = load_config(args.root) if args.root else default_config(Path.cwd())
    if args.model:
        config = replace(config, model=nonempty_text(args.model, "model"))
    if args.repair_round is not None and args.repair_round < 0:
        raise ValueError("repair round must be nonnegative")
    output = repository_path(config.root, args.out) if args.out else None
    if output and config.authority != "owned":
        raise ValueError("contributed mode forbids file writes; use stdout")
    cases, corpus = _inputs(args, config)
    if args.repair_round is not None:
        cases = [replace(case, repair_round=args.repair_round) for case in cases]
    previous = (
        as_object(
            read_json(args.previous_report.read_text(encoding="utf-8")),
            "previous report",
        )
        if args.previous_report
        else None
    )
    report = review_cases(
        config,
        cases,
        corpus=corpus,
        api_key=_key(args),
        previous=previous,
        functions=_functions(args.docstrings),
        new_paths=set(args.new),
        moved_paths=set(args.moved),
        function_repair_round=args.repair_round or 0,
        snapshot_filesystem=args.root is not None,
    )
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if output:
        output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return _exit_code(config, report)
