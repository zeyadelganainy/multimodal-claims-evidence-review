"""CSV input/output for the claim review pipeline."""

import csv
from pathlib import Path

INPUT_COLUMNS = ["user_id", "image_paths", "user_claim", "claim_object"]

OUTPUT_COLUMNS = [
    "user_id",
    "image_paths",
    "user_claim",
    "claim_object",
    "evidence_standard_met",
    "evidence_standard_met_reason",
    "risk_flags",
    "issue_type",
    "object_part",
    "claim_status",
    "claim_status_justification",
    "supporting_image_ids",
    "valid_image",
    "severity",
]


def read_claims(path: str | Path) -> list[dict]:
    """Read a claims CSV (claims.csv or sample_claims.csv) into row dicts."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_output(rows: list[dict], path: str | Path) -> None:
    """Write rows to output.csv with the exact required column order."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUTPUT_COLUMNS, quoting=csv.QUOTE_ALL
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in OUTPUT_COLUMNS})


def image_ids_for_row(row: dict) -> list[str]:
    """Extract image IDs (filename without extension) from a row's image_paths."""
    paths = row["image_paths"].split(";")
    return [Path(p).stem for p in paths]


def case_id_for_row(row: dict) -> str | None:
    """Extract the case_XXX folder name from a row's image_paths, if present."""
    import re

    match = re.search(r"case_\d+", row["image_paths"])
    return match.group(0) if match else None
