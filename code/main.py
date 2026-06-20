"""End-to-end terminal entry point for the multi-modal evidence review pipeline.

For each claim row:
  1. resolve and existence-check the submitted images
  2. (all missing -> deterministic unassessable NEI row, no model call)
  3. run the deterministic duplicate-image hash check
  4. look up user history + evidence requirements
  5. one structured multimodal model call (component 4)
  6. deterministic validation / assembly into the output row (component 5)

A per-row model failure (refusal / truncation / missing files) is caught and
turned into a deterministic NEI + manual_review_required row, so one bad claim
never sinks the whole batch.

Usage (from anywhere):
    python code/main.py                         # full dataset/claims.csv -> ./output.csv
    python code/main.py --cases case_007,case_055
    python code/main.py --limit 5 --output dev_output.csv

Reads ANTHROPIC_API_KEY from the environment (never hardcoded).
"""

import argparse
import sys
from pathlib import Path

# Allow running from any cwd: the sibling modules import each other by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic

from image_utils import find_duplicate_pairs, resolve_image_paths, validate_images_exist
from io_utils import case_id_for_row, read_claims, write_output
from llm_client import ClaimReviewError, call_claim_review
from lookups import (
    get_evidence_requirements,
    get_user_history,
    load_evidence_requirements,
    load_user_history,
)
from validation import assemble_output_row, build_unassessable_row

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLAIMS = REPO_ROOT / "dataset" / "claims.csv"
DEFAULT_DATASET_ROOT = REPO_ROOT / "dataset"
DEFAULT_OUTPUT = REPO_ROOT / "output.csv"
DEFAULT_HISTORY = REPO_ROOT / "dataset" / "user_history.csv"
DEFAULT_REQUIREMENTS = REPO_ROOT / "dataset" / "evidence_requirements.csv"

# Claude Opus 4.8 list price, USD per 1M tokens. The static system+examples
# prefix is cached (see llm_client.call_claim_review) -- the first call in a
# run pays the cache-write rate, every call after that pays the much cheaper
# cache-read rate for that prefix.
PRICE_PER_MTOK = {
    "input_tokens": 5.00,
    "output_tokens": 25.00,
    "cache_creation_input_tokens": 6.25,   # 5-minute write, 1.25x input
    "cache_read_input_tokens": 0.50,       # 0.1x input
}


def usage_cost(usage: dict) -> float:
    """Dollar cost of one call's usage dict, by token tier."""
    return sum(
        usage.get(field, 0) / 1_000_000 * rate
        for field, rate in PRICE_PER_MTOK.items()
    )


def process_row(row, dataset_root, history_index, requirements, client):
    """Run the full pipeline for one claim row.

    Returns (output_row_dict, usage_dict_or_None) -- usage is None on the
    deterministic no-API paths (all images missing, or a model failure)."""
    history = get_user_history(history_index, row["user_id"])

    paths = resolve_image_paths(row, dataset_root)
    missing = validate_images_exist(paths)
    present = [p for p in paths if p not in missing]

    if not present:
        return build_unassessable_row(
            row, history, "No submitted image files could be located for this claim."
        ), None

    duplicate_pairs = find_duplicate_pairs(present)
    evidence_requirements = get_evidence_requirements(requirements, row["claim_object"])

    try:
        result = call_claim_review(
            row, present, history, evidence_requirements, duplicate_pairs, client=client
        )
    except ClaimReviewError as exc:
        reason = f"Automated review could not complete ({exc.stop_reason}); routed to manual review."
        return build_unassessable_row(row, history, reason), None
    except anthropic.APIError as exc:
        # Any other API-level failure (oversized payload, rate limit, server
        # error, etc.) -- one bad row must never sink the whole batch.
        reason = f"Automated review could not complete (API error: {exc}); routed to manual review."
        return build_unassessable_row(row, history, reason), None

    out = assemble_output_row(row, result["findings"], history, duplicate_pairs)
    return out, result["usage"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Multi-modal evidence review pipeline.")
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N rows.")
    parser.add_argument("--cases", type=str, default=None,
                        help="Comma-separated case_ids to process (e.g. case_007,case_055).")
    args = parser.parse_args(argv)

    rows = read_claims(args.claims)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",")}
        rows = [r for r in rows if case_id_for_row(r) in wanted]
    if args.limit is not None:
        rows = rows[: args.limit]

    if not rows:
        print("No matching claim rows to process.", file=sys.stderr)
        return 1

    history_index = load_user_history(args.history)
    requirements = load_evidence_requirements(args.requirements)
    client = anthropic.Anthropic()

    outputs = []
    totals = {field: 0 for field in PRICE_PER_MTOK}
    total_cost = 0.0
    api_calls = 0
    for i, row in enumerate(rows, 1):
        case_id = case_id_for_row(row) or row["user_id"]
        out, usage = process_row(row, args.dataset_root, history_index, requirements, client)
        if usage is not None:
            api_calls += 1
            for field in totals:
                totals[field] += usage.get(field, 0)
            total_cost += usage_cost(usage)
        print(f"[{i}/{len(rows)}] {case_id}: {out['claim_status']} "
              f"| evidence_standard_met={out['evidence_standard_met']} "
              f"| valid_image={out['valid_image']} | risk_flags={out['risk_flags']}")
        outputs.append(out)
        # Write after every row, not just at the end -- a crash on row N must
        # not lose the (already paid-for) work done on rows 1..N-1.
        write_output(outputs, args.output)
    print(f"\nWrote {len(outputs)} rows -> {args.output}")
    print(f"API calls: {api_calls}  |  input tokens: {totals['input_tokens']:,}  "
          f"|  output tokens: {totals['output_tokens']:,}")
    if totals["cache_read_input_tokens"] or totals["cache_creation_input_tokens"]:
        print(f"cache read: {totals['cache_read_input_tokens']:,}  "
              f"|  cache write: {totals['cache_creation_input_tokens']:,}")
    print(f"Estimated cost: ${total_cost:.4f}"
          + (f"  (avg ${total_cost / api_calls:.4f}/call)" if api_calls else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
