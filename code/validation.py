"""Deterministic validation / guardrail layer (component 5).

Consumes the raw model findings plus deterministic context (the user-history
lookup and the duplicate-hash results) and assembles one output.csv row,
enforcing every locked invariant.

Trusts the model for visual judgment -- what's visible, per-image quality and
authenticity, severity, issue type, object_part (damage location), and the
descriptive risk flags -- and overrides wherever a rule must be deterministic:

  * claim_status cascade: evidence-insufficiency NEI > fraud-deny >
    claim-side NEI (vague / ambiguous) > the model's verdict (insufficiency
    comes first so an authenticity problem on an image that was never going
    to be informative anyway -- e.g. wrong-angle and also stock-styled --
    doesn't get reframed as a confident "contradicted")
  * evidence_standard_met as the strict complement of NEI (locked biconditional)
  * severity / issue_type consistency (NEI -> unknown/unknown; else none<->none)
  * history-risk flags copied from history_flags, with the user_history_risk ->
    manual_review_required pairing enforced
  * text_instruction_present -> manual_review_required pairing enforced (any
    injection attempt gets escalated for human review, but never changes
    claim_status itself -- that stays driven purely by visual evidence)
  * supporting_image_ids restricted to IDs that exist in this row
  * valid_image = any legible, authentic, non-duplicate image
  * risk_flags clamped to the allowed set, deduped, ordered to match the spec
"""

from io_utils import image_ids_for_row
from llm_client import ISSUE_TYPES, MODEL_RISK_FLAGS, OBJECT_PARTS

# Full risk_flags vocabulary, in the order output.csv serializes them.
ALLOWED_RISK_FLAGS = [
    "none", "blurry_image", "cropped_or_obstructed", "low_light_or_glare",
    "wrong_angle", "wrong_object", "wrong_object_part", "damage_not_visible",
    "claim_mismatch", "possible_manipulation", "non_original_image",
    "text_instruction_present", "user_history_risk", "manual_review_required",
]
_FLAG_ORDER = {flag: i for i, flag in enumerate(ALLOWED_RISK_FLAGS)}

_DAMAGE_SEVERITIES = {"low", "medium", "high"}
_CLAIM_STATUSES = {"supported", "contradicted", "not_enough_information"}

# authenticity_issue value -> the risk flag it maps to
_AUTHENTICITY_FLAG = {
    "stock_photo_or_watermark": "non_original_image",
    "photo_of_printed_photo": "non_original_image",
    "visible_editing": "possible_manipulation",
    "screenshot_of_screenshot": "possible_manipulation",
}


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


def _serialize(values) -> str:
    """Semicolon-join a flag/id list; the literal 'none' when empty."""
    values = list(values)
    return ";".join(values) if values else "none"


def _order_flags(flags: set) -> list:
    return sorted(flags, key=lambda f: _FLAG_ORDER[f])


def _history_flag_tokens(history: dict) -> set:
    """The allowed risk-flag tokens carried directly in history_flags."""
    history_flags = history.get("history_flags") or "none"
    return {
        token for token in history_flags.split(";")
        if token in ALLOWED_RISK_FLAGS and token != "none"
    }


def assemble_output_row(row, findings, history, duplicate_pairs):
    """Build one validated output.csv row dict (all values are strings)."""
    claim_object = row["claim_object"]
    actual_ids = image_ids_for_row(row)
    per_image = findings.get("per_image", [])

    # ---- deterministic signals ----
    images_sufficient = any(img.get("sufficient_for_claim") for img in per_image)
    authenticity_failed_ids = {
        img.get("image_id") for img in per_image
        if img.get("authenticity_issue", "none") != "none"
    }
    model_supporting_ids = {
        sid for sid in findings.get("supporting_image_ids", []) if sid in actual_ids
    }
    if findings.get("claim_status") == "supported" and model_supporting_ids:
        # A multi-part claim can be genuinely supported by one clean image while
        # a *different* image (addressing an unrelated part) has its own
        # authenticity problem -- that shouldn't poison an independently
        # confirmed part. Scope the check to the image(s) actually relied on.
        authenticity_failed = bool(model_supporting_ids & authenticity_failed_ids)
    else:
        authenticity_failed = bool(authenticity_failed_ids)
    has_duplicates = bool(duplicate_pairs)
    fraud_deny = authenticity_failed or has_duplicates

    vague = bool(findings.get("claim_text_too_vague"))
    ambiguous = bool(findings.get("ambiguous_object_identity"))
    extra_damage = bool(findings.get("extra_unclaimed_damage_observed"))

    # ---- claim_status cascade (priority order) ----
    # Insufficiency comes first: if no image gives any confident signal about
    # the claim at all, that's NEI regardless of an authenticity problem on
    # an image that wasn't going to be informative either way (e.g. a clean
    # but wrong-angle photo that's also stock-styled -- there's nothing for
    # the authenticity issue to "contradict"). fraud_deny only kicks in once
    # there's otherwise-sufficient evidence being denied by the authenticity
    # problem itself.
    if not images_sufficient:
        claim_status = "not_enough_information"
    elif fraud_deny:
        claim_status = "contradicted"
    elif vague or ambiguous:
        claim_status = "not_enough_information"
    else:
        claim_status = findings.get("claim_status", "not_enough_information")
        if claim_status not in _CLAIM_STATUSES:
            claim_status = "not_enough_information"

    is_nei = claim_status == "not_enough_information"

    # evidence_standard_met is the strict complement of NEI (locked biconditional)
    evidence_standard_met = not is_nei

    # ---- severity & issue_type ----
    if is_nei:
        severity = "unknown"
        issue_type = "unknown"
    else:
        severity = findings.get("severity", "none")
        if severity not in _DAMAGE_SEVERITIES and severity != "none":
            severity = "none"  # 'unknown' is NEI-only; clamp anything stray
        issue_type = findings.get("confirmed_issue_type", "none")
        if issue_type not in ISSUE_TYPES:
            issue_type = "unknown"
        if severity == "none":
            issue_type = "none"        # none <-> none
        elif issue_type == "none":
            issue_type = "unknown"     # damage present -> type can't be none

    # ---- object_part: the model's damage-location judgment, clamped ----
    object_part = findings.get("object_part", "unknown")
    if object_part not in OBJECT_PARTS.get(claim_object, ()):
        object_part = "unknown"

    # ---- supporting_image_ids: subset of this row's IDs; empty on NEI ----
    if is_nei:
        supporting = []
    else:
        supporting = [
            sid for sid in findings.get("supporting_image_ids", []) if sid in actual_ids
        ]

    # ---- valid_image: any legible, authentic, non-duplicate image ----
    dup_ids = {p["a"] for p in duplicate_pairs} | {p["b"] for p in duplicate_pairs}
    valid_image = any(
        img.get("legible")
        and img.get("authenticity_issue", "none") == "none"
        and img.get("image_id") not in dup_ids
        for img in per_image
    )

    # ---- risk_flags ----
    flags = set()
    # model-proposed descriptive flags (clamped to the model subset, no 'none')
    for flag in findings.get("risk_flags", []):
        if flag in MODEL_RISK_FLAGS and flag != "none":
            flags.add(flag)
    # deterministic authenticity flags (belt-and-suspenders with the model)
    for img in per_image:
        mapped = _AUTHENTICITY_FLAG.get(img.get("authenticity_issue", "none"))
        if mapped:
            flags.add(mapped)
    if has_duplicates:
        flags.add("possible_manipulation")
    # in-image instruction text (conversation-text injection is flagged by the model)
    if any(img.get("text_instruction_in_image") for img in per_image):
        flags.add("text_instruction_present")
    # history flags copied straight from history_flags (same vocabulary)
    flags |= _history_flag_tokens(history)
    if "user_history_risk" in flags:
        flags.add("manual_review_required")   # locked pairing (rule 13)
    if "text_instruction_present" in flags:
        flags.add("manual_review_required")   # any injection attempt gets human review,
        # but never changes claim_status itself -- that's still driven purely by
        # visual evidence (see the model prompt's injection-immunity rule).
    # escalate anything we couldn't auto-resolve, or that denied for fraud
    if is_nei or fraud_deny or extra_damage:
        flags.add("manual_review_required")
    # invariant: valid_image=false must carry at least one flag
    if not valid_image and not flags:
        flags.add("manual_review_required")

    return {
        "user_id": row["user_id"],
        "image_paths": row["image_paths"],
        "user_claim": row["user_claim"],
        "claim_object": claim_object,
        "evidence_standard_met": _bool_str(evidence_standard_met),
        "evidence_standard_met_reason": findings.get("evidence_standard_met_reason", ""),
        "risk_flags": _serialize(_order_flags(flags)),
        "issue_type": issue_type,
        "object_part": object_part,
        "claim_status": claim_status,
        "claim_status_justification": findings.get("claim_status_justification", ""),
        "supporting_image_ids": _serialize(supporting),
        "valid_image": _bool_str(valid_image),
        "severity": severity,
    }


def build_unassessable_row(row, history, reason):
    """Deterministic NEI row for the defensive case where a claim's image files
    are all missing on disk, so no model call was made."""
    flags = _history_flag_tokens(history)
    flags.add("manual_review_required")
    return {
        "user_id": row["user_id"],
        "image_paths": row["image_paths"],
        "user_claim": row["user_claim"],
        "claim_object": row["claim_object"],
        "evidence_standard_met": "false",
        "evidence_standard_met_reason": reason,
        "risk_flags": _serialize(_order_flags(flags)),
        "issue_type": "unknown",
        "object_part": "unknown",
        "claim_status": "not_enough_information",
        "claim_status_justification": reason,
        "supporting_image_ids": "none",
        "valid_image": "false",
        "severity": "unknown",
    }
