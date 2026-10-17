"""Inspect documentation without installing hooks or contacting a model by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docreview.configuration import load_config
from docreview.documentation_trial import main as trial_main
from docreview.facts import inspect_repository, report_exit_code
from docreview.paths import repository_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: docreview facts --root /path/to/repo docs/usage/recover.md",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "review",
        help="Run the moved trial; preview only unless --live is supplied",
        add_help=False,
    )
    facts = commands.add_parser(
        "facts", help="Check maintained Markdown without a model or credentials"
    )
    facts.add_argument(
        "paths",
        nargs="*",
        help="Selected repository-relative pages; default all maintained pages",
    )
    facts.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository containing docs-review.toml",
    )
    facts.add_argument(
        "--new",
        action="append",
        default=[],
        metavar="PATH",
        help="New selected page: enforce substantial-page inbound routing",
    )
    facts.add_argument(
        "--moved",
        action="append",
        default=[],
        metavar="PATH",
        help="Moved selected page: route placement decisions to review",
    )
    facts.add_argument(
        "--out",
        metavar="PATH",
        help="Write JSON inside an owned repository; otherwise print to stdout",
    )
    commands.add_parser(
        "catalog", help="Reserved for the later catalog-generator slice"
    )
    adopt = commands.add_parser(
        "adopt", help="Reserved for the later adoption-report slice"
    )
    adopt.add_argument("--report-only", action="store_true", required=True)
    return parser


def _facts(args: argparse.Namespace) -> int:
    config = load_config(args.root)
    output = repository_path(config.root, args.out) if args.out else None
    if output and config.authority == "contributed":
        raise ValueError("contributed mode forbids file writes; use stdout")
    report = inspect_repository(
        config, args.paths or None, new_paths=set(args.new), moved_paths=set(args.moved)
    )
    payload = json.dumps(report, indent=2) + "\n"
    if output:
        output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return report_exit_code(config, report)


def main(argv: list[str] | None = None) -> int:
    """Return 1 for owned enforcing failures, 2 for incomplete runs, otherwise 0."""
    parser = _parser()
    args, remaining = parser.parse_known_args(argv)
    if args.command == "review":
        return trial_main(remaining)
    if remaining:
        parser.error("unrecognized arguments: " + " ".join(remaining))
    try:
        if args.command == "facts":
            return _facts(args)
        print(
            f"{args.command} is not implemented in this package slice", file=sys.stderr
        )
        return 2
    except ValueError as exc:
        print(f"Documentation checks could not complete: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"Documentation checks could not read or write a configured path ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2
