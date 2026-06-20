"""Scratch smoke test for io_utils — not part of the pipeline, delete after review."""

from io_utils import read_claims, write_output, image_ids_for_row, case_id_for_row

rows = read_claims("../dataset/claims.csv")
print(f"Read {len(rows)} rows from claims.csv\n")

for row in rows[:3]:
    print("user_id:", row["user_id"])
    print("claim_object:", row["claim_object"])
    print("image_ids:", image_ids_for_row(row))
    print("case_id:", case_id_for_row(row))
    print("user_claim (first 60 chars):", row["user_claim"][:60])
    print("---")

# Placeholder decision columns to test the output writer round-trips passthrough correctly
out_rows = []
for row in rows[:3]:
    out_row = dict(row)
    out_row.update(
        {
            "evidence_standard_met": "PLACEHOLDER",
            "evidence_standard_met_reason": "PLACEHOLDER",
            "risk_flags": "PLACEHOLDER",
            "issue_type": "PLACEHOLDER",
            "object_part": "PLACEHOLDER",
            "claim_status": "PLACEHOLDER",
            "claim_status_justification": "PLACEHOLDER",
            "supporting_image_ids": "PLACEHOLDER",
            "valid_image": "PLACEHOLDER",
            "severity": "PLACEHOLDER",
        }
    )
    out_rows.append(out_row)

write_output(out_rows, "_smoke_test_output.csv")
print("Wrote _smoke_test_output.csv")
