"""Scratch smoke test for lookups.py — not part of the pipeline, kept for records."""

from lookups import (
    load_user_history,
    get_user_history,
    load_evidence_requirements,
    get_evidence_requirements,
)

history = load_user_history("../dataset/user_history.csv")
requirements = load_evidence_requirements("../dataset/evidence_requirements.csv")

print(f"Loaded {len(history)} users, {len(requirements)} requirement rows\n")

# A clean user, the worst-history user (used in case_051), and a manual_review_required-only user
for user_id in ["user_001", "user_016", "user_032", "user_999_missing"]:
    h = get_user_history(history, user_id)
    print(f"{user_id}: history_flags={h['history_flags']!r} | "
          f"past={h['past_claim_count']} rejected={h['rejected_claim']} "
          f"last_90d={h['last_90_days_claim_count']}")
    print(f"  summary: {h['history_summary']}")

print()

for claim_object in ["car", "laptop", "package"]:
    reqs = get_evidence_requirements(requirements, claim_object)
    print(f"{claim_object}: {len(reqs)} applicable requirement rows")
    for r in reqs:
        print(f"  - {r['requirement_id']} ({r['claim_object']}/{r['applies_to']})")
