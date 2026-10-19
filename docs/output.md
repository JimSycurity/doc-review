---
type: reference
---

# Report and exit codes

Every `facts` and `review` run writes one JSON object to stdout, or to `--out`
in `owned` mode. This page defines its fields and the exit codes. Field names
are stable: new fields may be added, none renamed or removed without a new
`policy_version`.

## Top level

| Field | Meaning |
| --- | --- |
| `policy_version` | report contract version, currently `docreview-1` |
| `engine_version` | running package version |
| `config_sha256` | SHA-256 of `docs-review.toml`; the hash of empty input when there is none |
| `generated_at` | UTC timestamp |
| `mode` | review only: `dry-run` or `live` |
| `model` | `requested` model ID and the `answered` version, or `null` |
| `thresholds` | the routing bands used |
| `warnings` | declaration warnings, such as a type glob that matches nothing |
| `documents` | one entry per page, case or docstring file |
| `duplication_index` | facts only: index metadata, including skipped sections and cache state |
| `summary` | counts: `documents`, `pass`, `revise`, `review`, `hard_failures`, `model_failures`; review adds `request_count` and `request_strategies` |
| `evaluation` | review only: label comparison, or `null` when there are no labels or prior report |

## Document entry

| Field | Meaning |
| --- | --- |
| `path` | repository-relative path |
| `type`, `type_source` | resolved type and how: `fixed_name`, `frontmatter`, `paths`, `runtime_schema`, or `null` |
| `status` | frontmatter status or `null` |
| `facts` | the [deterministic facts](checks.md#facts-that-never-fail) |
| `hard_failures` | located rule violations: `rule`, `line_start`, `line_end`, `message` |
| `requests` | model requests: `sha256`, `bytes`, `strategy`, `model`, `usage`, `seconds`, `error`; dry-run adds `preview` |
| `criteria` | per criterion: `question`, `family`, `location`, `status` (`pass`, `fail` or `uncertain`), `probability_met`, `answer`, `enforcement`, and sample answers and decision signals when assessed |
| `route` | `pass`, `revise` or `review` |
| `reasons` | short reason codes for the route |
| `handoff` | `target`, `failed`, `uncertain`, `located_items`, `instructions`; review adds `prior_findings` |
| `not_assessed` | what this run could not judge, in plain words |
| `case_id`, `coverage_gaps` | review only: case name and located gaps such as `request_too_large` |

A facts-only run never passes a page. Its `route` is `revise` when a hard
failure exists and `review` otherwise.

Example, shortened:

```json
{
  "policy_version": "docreview-1",
  "engine_version": "0.1.0",
  "config_sha256": "...",
  "generated_at": "2026-10-17T00:00:00+00:00",
  "model": { "requested": "typesafe/jev-1.14", "answered": null },
  "thresholds": { "accept_above": 0.9, "reject_below": 0.1, "min_confidence": 0.8,
                  "type_resolution": 0.85, "duplication_same": 0.8 },
  "documents": [
    {
      "path": "docs/usage/recover-worker.md",
      "type": "runbook",
      "type_source": "frontmatter",
      "status": null,
      "facts": { "bytes": 0, "sections": 0, "inbound_links": 0, "banned_terms": [] },
      "hard_failures": [
        { "rule": "frontmatter.unknown_key", "line_start": 3, "line_end": 3,
          "message": "frontmatter key 'tags' is not allowed; ..." }
      ],
      "requests": [],
      "criteria": {},
      "route": "revise",
      "reasons": ["deterministic_requirement_failure"],
      "handoff": { "target": "authoring_agent", "failed": ["frontmatter.unknown_key"],
                   "uncertain": [], "located_items": [], "instructions": "..." },
      "not_assessed": ["model criteria: facts-only run"]
    }
  ],
  "summary": { "documents": 1, "pass": 0, "revise": 1, "review": 0,
               "hard_failures": 1, "model_failures": 0 },
  "warnings": []
}
```

## Exit codes

| Code | `facts` and `review` | `catalog` |
| --- | --- | --- |
| `0` | the run completed; in `contributed` mode, always when it completed | catalogs current or written |
| `1` | `owned` mode only: a hard failure, or an enforcing model criterion failed | `owned` mode only: a stale catalog under `--check`, or a catalog failure |
| `2` | the run could not complete: invalid declaration, unreadable path, refused write or a provider failure | invalid declaration or unreadable path |

`contributed` mode exits only `0` or `2`. Keep earlier reports when you need
to compare runs or supply `--previous-report`.
