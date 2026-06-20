"""Streamlit viewer for the multi-modal evidence review pipeline.

Browses an already-generated output CSV (output.csv or
evaluation/sample_predictions.csv) row by row: the claim conversation, every
submitted image, and the model's structured verdict side by side. Read-only
by default; optionally re-runs a single row live against the Anthropic API.

Run with:
    streamlit run code/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from io_utils import OUTPUT_COLUMNS, image_ids_for_row
from lookups import (
    get_evidence_requirements,
    get_user_history,
    load_evidence_requirements,
    load_user_history,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
DEFAULT_HISTORY_PATH = DATASET_ROOT / "user_history.csv"
DEFAULT_REQUIREMENTS_PATH = DATASET_ROOT / "evidence_requirements.csv"

STATUS_COLOR = {
    "supported": "#1a7f37",
    "contradicted": "#cf222e",
    "not_enough_information": "#9a6700",
}

st.set_page_config(page_title="Evidence Review Viewer", layout="wide")


@st.cache_data
def load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


@st.cache_data
def load_history(path: Path):
    return load_user_history(path)


@st.cache_data
def load_requirements(path: Path):
    return load_evidence_requirements(path)


def resolve_image_path(row: dict, raw_path: str) -> Path | None:
    candidate = DATASET_ROOT / raw_path
    if candidate.exists():
        return candidate
    # claims.csv/sample_claims.csv image_paths are relative to dataset/;
    # output.csv preserves the same column verbatim, so this should
    # normally resolve directly.
    return None


st.title("Multi-Modal Evidence Review — Claim Viewer")
st.caption(
    "Read-only viewer over a generated output CSV — built for HackerRank "
    "Orchestrate (June 2026). See code/README.md for the pipeline this "
    "visualizes."
)

default_outputs = {
    "Test set (output.csv)": REPO_ROOT / "output.csv",
    "Sample set (evaluation/sample_predictions.csv)": (
        REPO_ROOT / "code" / "evaluation" / "sample_predictions.csv"
    ),
}
available = {label: p for label, p in default_outputs.items() if p.exists()}

if not available:
    st.error(
        "No output CSV found. Run `python code/main.py` or "
        "`python code/evaluation/main.py` first."
    )
    st.stop()

source_label = st.sidebar.selectbox("Output source", list(available.keys()))
df = load_csv(available[source_label])

statuses = ["All"] + sorted(df["claim_status"].unique())
status_filter = st.sidebar.selectbox("Filter by claim_status", statuses)
filtered = df if status_filter == "All" else df[df["claim_status"] == status_filter]

st.sidebar.metric("Rows shown", len(filtered))
st.sidebar.metric(
    "supported", int((filtered["claim_status"] == "supported").sum())
)
st.sidebar.metric(
    "contradicted", int((filtered["claim_status"] == "contradicted").sum())
)
st.sidebar.metric(
    "not_enough_information",
    int((filtered["claim_status"] == "not_enough_information").sum()),
)

if filtered.empty:
    st.warning("No rows match this filter.")
    st.stop()

row_labels = [
    f"{i} — {r['claim_object']} — {r['claim_status']}"
    for i, r in filtered.reset_index(drop=True).iterrows()
]
selected = st.sidebar.radio("Claim", row_labels, label_visibility="collapsed")
selected_idx = row_labels.index(selected)
row = filtered.reset_index(drop=True).iloc[selected_idx].to_dict()

left, right = st.columns([1, 1])

with left:
    st.subheader(f"Claim — {row['claim_object']}")
    color = STATUS_COLOR.get(row["claim_status"], "#57606a")
    st.markdown(
        f"**claim_status:** <span style='color:{color}; font-weight:700'>"
        f"{row['claim_status']}</span>",
        unsafe_allow_html=True,
    )
    st.text(row["user_claim"])

    st.markdown("**Images**")
    image_ids = image_ids_for_row(row)
    raw_paths = row["image_paths"].split(";")
    cols = st.columns(min(3, len(raw_paths)) or 1)
    for i, (img_id, raw_path) in enumerate(zip(image_ids, raw_paths)):
        path = resolve_image_path(row, raw_path)
        with cols[i % len(cols)]:
            if path:
                st.image(str(path), caption=img_id, use_container_width=True)
            else:
                st.warning(f"{img_id}: file not found ({raw_path})")
            supporting = row.get("supporting_image_ids", "")
            if img_id in supporting.split(";"):
                st.caption("✓ supporting image")

with right:
    st.subheader("Structured verdict")
    fields = [c for c in OUTPUT_COLUMNS if c not in ("user_claim", "image_paths")]
    for field in fields:
        value = row.get(field, "")
        if not value:
            continue
        st.markdown(f"**{field}**")
        st.text(value)

    st.markdown("**Minimum evidence requirements** (`claim_object` lookup)")
    if DEFAULT_REQUIREMENTS_PATH.exists():
        requirements = load_requirements(DEFAULT_REQUIREMENTS_PATH)
        applicable = get_evidence_requirements(requirements, row["claim_object"])
        st.dataframe(pd.DataFrame(applicable), hide_index=True, use_container_width=True)

    st.markdown("**Claimant history** (`user_id` lookup — risk signal only, never visual verdict)")
    if DEFAULT_HISTORY_PATH.exists():
        history_index = load_history(DEFAULT_HISTORY_PATH)
        history = get_user_history(history_index, row["user_id"])
        st.json({k: v for k, v in history.items() if k != "user_id"})

st.divider()
st.caption(
    "Pipeline: code/main.py (component 4: llm_client.py, component 5: "
    "validation.py). This viewer makes no API calls — it only displays "
    "results that were already generated."
)
