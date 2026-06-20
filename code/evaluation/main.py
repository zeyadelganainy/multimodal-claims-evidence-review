"""Evaluation entry point: scores the pipeline against dataset/sample_claims.csv.

dataset/sample_claims.csv carries both the input columns and the expected
output columns in one file. This script strips each row down to the input
columns, runs it through the exact same pipeline as code/main.py (no
duplicated logic), and compares the result to the expected columns already
in the file.

Free-text fields (evidence_standard_met_reason, claim_status_justification)
are not scored -- there's no single correct phrasing to match against.
risk_flags and supporting_image_ids are semicolon-joined sets, so they're
compared as sets (order-insensitive); every other scored field is compared
as an exact string match.

Usage (from anywhere):
    python code/evaluation/main.py
    python code/evaluation/main.py --limit 5
    python code/evaluation/main.py --output predictions.csv

Reads ANTHROPIC_API_KEY from the environment (never hardcoded).
"""

import argparse
import csv
import sys
from pathlib import Path

# code/evaluation/main.py -> insert code/ so sibling modules import by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic

from io_utils import INPUT_COLUMNS, case_id_for_row, write_output
from lookups import load_evidence_requirements, load_user_history
from main import PRICE_PER_MTOK, process_row, usage_cost

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SAMPLE = REPO_ROOT / "dataset" / "sample_claims.csv"
DEFAULT_DATASET_ROOT = REPO_ROOT / "dataset"
DEFAULT_HISTORY = REPO_ROOT / "dataset" / "user_history.csv"
DEFAULT_REQUIREMENTS = REPO_ROOT / "dataset" / "evidence_requirements.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "sample_predictions.csv"

# Fields worth scoring exactly; the *_reason / *_justification free-text
# fields are excluded since there's no single correct phrasing.
SCORED_FIELDS = [
    "evidence_standard_met",
    "risk_flags",
    "issue_type",
    "object_part",
    "claim_status",
    "supporting_image_ids",
    "valid_image",
    "severity",
]
SET_FIELDS = {"risk_flags", "supporting_image_ids"}


def _field_match(field: str, expected: str, actual: str) -> bool:
    if field in SET_FIELDS:
        return set(expected.split(";")) == set(actual.split(";"))
    return expected == actual


def score_row(expected_row: dict, predicted_row: dict) -> dict:
    return {f: _field_match(f, expected_row[f], predicted_row[f]) for f in SCORED_FIELDS}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate the claim-review pipeline against dataset/sample_claims.csv."
    )
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Where to write the raw predictions for inspection.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Evaluate only the first N rows.")
    args = parser.parse_args(argv)

    with open(args.sample, newline="", encoding="utf-8") as f:
        expected_rows = list(csv.DictReader(f))
    if args.limit is not None:
        expected_rows = expected_rows[: args.limit]

    if not expected_rows:
        print("No rows to evaluate.", file=sys.stderr)
        return 1

    history_index = load_user_history(args.history)
    requirements = load_evidence_requirements(args.requirements)
    client = anthropic.Anthropic()

    field_correct = {f: 0 for f in SCORED_FIELDS}
    exact_match_rows = 0
    mismatches = []
    predictions = []
    totals = {f: 0 for f in PRICE_PER_MTOK}
    total_cost = 0.0
    api_calls = 0

    for i, expected_row in enumerate(expected_rows, 1):
        input_row = {k: expected_row[k] for k in INPUT_COLUMNS}
        case_id = case_id_for_row(input_row) or input_row["user_id"]

        predicted, usage = process_row(input_row, args.dataset_root, history_index, requirements, client)
        predictions.append(predicted)
        if usage is not None:
            api_calls += 1
            for field in totals:
                totals[field] += usage.get(field, 0)
            total_cost += usage_cost(usage)

        results = score_row(expected_row, predicted)
        row_exact = all(results.values())
        if row_exact:
            exact_match_rows += 1
        for field, ok in results.items():
            if ok:
                field_correct[field] += 1
            else:
                mismatches.append((case_id, field, expected_row[field], predicted[field]))

        n_ok = sum(results.values())
        print(f"[{i}/{len(expected_rows)}] {case_id}: "
              f"{'MATCH' if row_exact else 'DIFF'} ({n_ok}/{len(SCORED_FIELDS)} fields)")

    write_output(predictions, args.output)

    n = len(expected_rows)
    print(f"\n=== Field accuracy ({n} rows) ===")
    for field in SCORED_FIELDS:
        c = field_correct[field]
        print(f"  {field}: {c}/{n} ({c / n:.0%})")
    print(f"\nExact-match rows (all {len(SCORED_FIELDS)} scored fields): "
          f"{exact_match_rows}/{n} ({exact_match_rows / n:.0%})")

    if mismatches:
        print("\n=== Mismatches ===")
        for case_id, field, expected, actual in mismatches:
            print(f"  {case_id} [{field}]: expected={expected!r} actual={actual!r}")

    print(f"\nAPI calls: {api_calls}  |  input tokens: {totals['input_tokens']:,}  "
          f"|  output tokens: {totals['output_tokens']:,}")
    if totals["cache_read_input_tokens"] or totals["cache_creation_input_tokens"]:
        print(f"cache read: {totals['cache_read_input_tokens']:,}  "
              f"|  cache write: {totals['cache_creation_input_tokens']:,}")
    print(f"Estimated cost: ${total_cost:.4f}"
          + (f"  (avg ${total_cost / api_calls:.4f}/call)" if api_calls else ""))
    print(f"\nPredictions written -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
