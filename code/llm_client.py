"""Core multimodal LLM call: one structured-output request per claim.

This module only calls the model and returns its raw structured findings.
It does not enforce the locked decision rules -- that's the deterministic
validation layer (component 5), which trusts this output for judgment calls
(what's visible, per-image quality/authenticity) but overrides it wherever
a rule is supposed to be deterministic.
"""

import base64
import io
import json
from pathlib import Path

import anthropic
from PIL import Image

MODEL = "claude-opus-4-8"


class ClaimReviewError(Exception):
    """Raised when the model call does not yield usable structured findings.

    Carries the response's stop_reason so the pipeline driver can record a
    deliberate per-row outcome (e.g. manual_review_required) instead of letting
    one bad row crash the whole batch.
    """

    def __init__(self, message: str, stop_reason: str | None = None):
        super().__init__(message)
        self.stop_reason = stop_reason

ISSUE_TYPES = [
    "dent", "scratch", "crack", "glass_shatter", "broken_part", "missing_part",
    "torn_packaging", "crushed_packaging", "water_damage", "stain", "none", "unknown",
]

# The risk-flag values the model may propose. Excludes user_history_risk and
# manual_review_required -- those are added deterministically downstream (from
# the claimant's history and the review rules), never chosen by the model.
MODEL_RISK_FLAGS = [
    "none", "blurry_image", "cropped_or_obstructed", "low_light_or_glare",
    "wrong_angle", "wrong_object", "wrong_object_part", "damage_not_visible",
    "claim_mismatch", "possible_manipulation", "non_original_image",
    "text_instruction_present",
]

OBJECT_PARTS = {
    "car": ["front_bumper", "rear_bumper", "door", "hood", "windshield", "side_mirror",
            "headlight", "taillight", "fender", "quarter_panel", "body", "unknown"],
    "laptop": ["screen", "keyboard", "trackpad", "hinge", "lid", "corner", "port",
               "base", "body", "unknown"],
    "package": ["box", "package_corner", "package_side", "seal", "label", "contents",
                "item", "unknown"],
}

_PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
EXAMPLES = (_PROMPTS_DIR / "examples.md").read_text(encoding="utf-8")


def _per_image_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "image_id": {"type": "string"},
            "legible": {"type": "boolean"},
            "quality_issue": {"type": "string", "enum": ["none", "blurry", "low_light_or_glare", "cropped_or_obstructed"]},
            "object_match": {"type": "string", "enum": ["correct_object", "wrong_object", "unclear"]},
            "part_visible": {"type": "boolean"},
            "sufficient_for_claim": {"type": "boolean"},
            "authenticity_issue": {"type": "string", "enum": ["none", "stock_photo_or_watermark", "visible_editing", "screenshot_of_screenshot", "photo_of_printed_photo"]},
            "damage_present": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
            "text_instruction_in_image": {"type": "boolean"},
            "observation": {"type": "string"},
        },
        "required": [
            "image_id", "legible", "quality_issue", "object_match", "part_visible",
            "sufficient_for_claim", "authenticity_issue", "damage_present",
            "text_instruction_in_image", "observation",
        ],
        "additionalProperties": False,
    }


def build_output_schema(claim_object: str) -> dict:
    """Output schema scoped to this row's claim_object (object_part enum narrowed)."""
    object_parts = OBJECT_PARTS[claim_object]
    return {
        "type": "object",
        "properties": {
            "per_image": {"type": "array", "items": _per_image_schema()},
            "object_part": {"type": "string", "enum": object_parts},
            "confirmed_issue_type": {"type": "string", "enum": ISSUE_TYPES},
            "risk_flags": {"type": "array", "items": {"type": "string", "enum": MODEL_RISK_FLAGS}},
            "claim_status": {"type": "string", "enum": ["supported", "contradicted", "not_enough_information"]},
            "severity": {"type": "string", "enum": ["none", "low", "medium", "high", "unknown"]},
            "supporting_image_ids": {"type": "array", "items": {"type": "string"}},
            "claim_status_justification": {"type": "string"},
            "evidence_standard_met_reason": {"type": "string"},
            "claim_text_too_vague": {"type": "boolean"},
            "ambiguous_object_identity": {"type": "boolean"},
            "extra_unclaimed_damage_observed": {"type": "boolean"},
            "history_summary_pattern_match": {"type": "boolean"},
        },
        "required": [
            "per_image", "object_part", "confirmed_issue_type", "risk_flags", "claim_status",
            "severity", "supporting_image_ids", "claim_status_justification",
            "evidence_standard_met_reason", "claim_text_too_vague",
            "ambiguous_object_identity", "extra_unclaimed_damage_observed",
            "history_summary_pattern_match",
        ],
        "additionalProperties": False,
    }


# Image formats the API accepts, mapped from Pillow's format name.
_SUPPORTED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


MAX_BASE64_LEN = 10 * 1024 * 1024  # Anthropic's image.source.base64 size cap
MAX_DIMENSION = 1568  # Claude downscales beyond this anyway; avoids wasted bytes/tokens


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (media_type, base64 data) for an API-acceptable image.

    The dataset is inconsistent: files named .jpg may actually be PNG, AVIF,
    MPO (multi-frame JPEG), BMP, TIFF, etc -- and some are large enough that
    even a correctly-typed photo can blow past the API's 10MB base64 cap
    (worse, lossless PNG re-encoding of a large AVIF can balloon well past
    the original file size). So open with Pillow and decide from the real
    format: pass supported formats through untouched when they're small
    enough, and otherwise downscale to a sane resolution and re-encode as
    JPEG, backing off quality until the payload fits.
    """
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as img:
        fmt = img.format
        if fmt in _SUPPORTED_FORMATS:
            data = base64.standard_b64encode(raw).decode("utf-8")
            if len(data) <= MAX_BASE64_LEN:
                return _SUPPORTED_FORMATS[fmt], data
        # Unsupported format, or a supported one that's still oversized.
        converted = img.convert("RGB")
        if max(converted.size) > MAX_DIMENSION:
            converted.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        for quality in (90, 75, 60, 45, 30):
            buffer = io.BytesIO()
            converted.save(buffer, format="JPEG", quality=quality)
            data = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
            if len(data) <= MAX_BASE64_LEN:
                return "image/jpeg", data
        return "image/jpeg", data  # smallest attempt; let the API reject if still too big


def _image_block(path: Path) -> dict:
    media_type, data = _encode_image(path)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def build_user_content(
    row: dict,
    image_paths: list[Path],
    history: dict,
    evidence_requirements: list[dict],
    duplicate_pairs: list[dict],
) -> list[dict]:
    """Assemble the per-claim user message: context text, then one block per image."""
    req_lines = "\n".join(
        f"- {r['requirement_id']} ({r['claim_object']}/{r['applies_to']}): {r['minimum_image_evidence']}"
        for r in evidence_requirements
    )
    dup_lines = (
        "\n".join(f"- {p['a']} and {p['b']}: {p['kind']} duplicate (hash distance {p['distance']})" for p in duplicate_pairs)
        if duplicate_pairs else "None detected."
    )
    image_ids = [p.stem for p in image_paths]

    context = f"""## Claim

claim_object: {row['claim_object']}
submitted image_ids: {image_ids}

user_claim (conversation transcript):
{row['user_claim']}

## Evidence requirements for this object

{req_lines}

## Claimant history context

history_flags: {history['history_flags']}
history_summary: {history['history_summary']}
past_claim_count: {history['past_claim_count']}, rejected_claim: {history['rejected_claim']}, last_90_days_claim_count: {history['last_90_days_claim_count']}

(Remember: history context never changes claim_status, severity, issue_type, or object_part. It only informs history_summary_pattern_match.)

## Deterministic duplicate-image check (already run in code, not your judgment call)

{dup_lines}

## Images

The images below are provided in this order: {image_ids}
"""

    content = [{"type": "text", "text": context}]
    for path in image_paths:
        content.append(_image_block(path))
    return content


def call_claim_review(
    row: dict,
    image_paths: list[Path],
    history: dict,
    evidence_requirements: list[dict],
    duplicate_pairs: list[dict],
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Run the single per-claim multimodal call. Returns the parsed JSON findings."""
    client = client or anthropic.Anthropic()
    user_content = build_user_content(row, image_paths, history, evidence_requirements, duplicate_pairs)

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[
            # Single cache breakpoint at the end of EXAMPLES caches the whole
            # static prefix (SYSTEM_PROMPT + EXAMPLES) as one unit -- both are
            # identical across every call in a run, only the per-claim user
            # content (built below) varies.
            {"type": "text", "text": SYSTEM_PROMPT},
            {"type": "text", "text": EXAMPLES, "cache_control": {"type": "ephemeral"}},
        ],
        output_config={"format": {"type": "json_schema", "schema": build_output_schema(row["claim_object"])}},
        messages=[{"role": "user", "content": user_content}],
    )

    # Guard the structured-output contract before parsing. output_config.format
    # only guarantees valid JSON when the model finished normally; a refusal
    # (plausible on the adversarial prompt-injection rows) yields empty/partial
    # content, and max_tokens yields truncated JSON. Either way json.loads would
    # crash, so surface a typed error the driver can handle per-row.
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None)
        raise ClaimReviewError(f"model refused: {detail}", stop_reason="refusal")
    if response.stop_reason == "max_tokens":
        raise ClaimReviewError("response truncated at max_tokens", stop_reason="max_tokens")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ClaimReviewError("no text block in response", stop_reason=response.stop_reason)

    return {"findings": json.loads(text), "usage": response.usage.model_dump()}
