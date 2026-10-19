"""Inspect documentation without installing hooks or contacting a model by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docreview.catalog import apply_catalogs, plan_catalogs
from docreview.configuration import load_config
from docreview.facts import inspect_repository, report_exit_code
from docreview.paths import repository_path
from docreview.review_cli import add_arguments, run_review


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: docreview facts --root /path/to/repo docs/usage/recover.md",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser(
        "review", help="Review facts and located criteria; dry-run by default"
    )
    add_arguments(review)
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
    catalog = commands.add_parser(
        "catalog", help="Generate or check declared directory catalogs"
    )
    catalog.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository containing docs-review.toml",
    )
    catalog.add_argument(
        "--check", action="store_true", help="Report stale catalogs without writing"
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


def _catalog(args: argparse.Namespace) -> int:
    config = load_config(args.root)
    plan = plan_catalogs(config)
    mode = (
        "report-only"
        if config.authority == "contributed"
        else "check"
        if args.check
        else "generate"
    )
    should_apply = mode == "generate" and not plan.failures
    if should_apply:
        apply_catalogs(config, plan)
    print(json.dumps(plan.as_json(mode, applied=should_apply), indent=2))
    return int(
        config.authority == "owned"
        and (bool(plan.failures) or (args.check and plan.has_failures))
    )


def main(argv: list[str] | None = None) -> int:
    """Return 1 for owned enforcing failures, 2 for incomplete runs, otherwise 0."""
    parser = _parser()
    args, remaining = parser.parse_known_args(argv)
    if remaining:
        parser.error("unrecognized arguments: " + " ".join(remaining))
    try:
        if args.command == "review":
            return run_review(args)
        if args.command == "facts":
            return _facts(args)
        if args.command == "catalog":
            return _catalog(args)
        print(
            f"{args.command} is not implemented in this package slice", file=sys.stderr
        )
        return 2
    except (ValueError, TypeError) as exc:
        print(f"Documentation checks could not complete: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"Documentation checks could not read or write a configured path ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2
