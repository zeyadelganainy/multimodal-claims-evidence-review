"""Deterministic lookups for user_history.csv and evidence_requirements.csv.

No retrieval/BM25 here on purpose: user_history is keyed exactly by user_id,
and evidence_requirements has ~11 rows keyed by claim_object. Both are flat
dict lookups, not search problems.
"""

import csv
from pathlib import Path

DEFAULT_HISTORY = {
    "user_id": "",
    "past_claim_count": "0",
    "accept_claim": "0",
    "manual_review_claim": "0",
    "rejected_claim": "0",
    "last_90_days_claim_count": "0",
    "history_flags": "none",
    "history_summary": "No history on file for this user.",
}


def load_user_history(path: str | Path) -> dict[str, dict]:
    """Load user_history.csv into a dict keyed by user_id."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["user_id"]: row for row in reader}


def get_user_history(history: dict[str, dict], user_id: str) -> dict:
    """Look up one user's history, falling back to a clean default if absent."""
    return history.get(user_id, {**DEFAULT_HISTORY, "user_id": user_id})


def load_evidence_requirements(path: str | Path) -> list[dict]:
    """Load evidence_requirements.csv as a flat list of requirement rows."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_evidence_requirements(requirements: list[dict], claim_object: str) -> list[dict]:
    """Return requirement rows that apply to this claim_object: the
    object-specific rows plus the universal ("all") rows."""
    return [r for r in requirements if r["claim_object"] in (claim_object, "all")]
