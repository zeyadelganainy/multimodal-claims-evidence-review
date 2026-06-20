# Multi-Modal Claims Evidence Review

A claim-verification system that decides whether photo evidence **supports**, **contradicts**,
or gives **not enough information** to judge a damage claim — for cars, laptops, and packages —
combining a vision-language model with a deterministic guardrail layer so the final decision
never depends purely on the model getting every business rule right unsupervised.

Built solo in 24 hours for **HackerRank Orchestrate (June 2026)**, a multi-modal evidence review
hackathon. The brief is preserved verbatim in [`problem_statement.md`](./problem_statement.md);
[`AGENTS.md`](./AGENTS.md) is the build-process governance file that directed how AI coding tools
(Claude Code, in this case) were allowed to operate in this repo, including mandatory per-turn
conversation logging during development.

## What it does

Given a short claim conversation, one or more submitted photos, the claimant's history, and the
object's minimum-evidence requirements, the system returns a structured verdict: whether the
evidence standard is met, the visible issue type and severity, which images actually drove the
decision, and a set of risk flags (blurry/cropped images, wrong object, authenticity problems,
prompt-injection attempts, history-based risk, etc.) — all grounded in what's actually visible in
the photos, never in how the claimant describes the damage.

## Demo

A read-only Streamlit viewer browses any generated output CSV: claim text, every submitted image,
and the full structured verdict side by side.

```bash
streamlit run code/app.py
```

<!-- screenshot: docs/app_screenshot.png -->
*(Run it locally to see the claim viewer — sidebar filters by `claim_status`, main panel shows
images and the full structured verdict side by side.)*

## Architecture

```
dataset/claims.csv ──► main.py ──► output.csv
                          │
                          ├─ io_utils.py        read claims.csv / write output.csv
                          ├─ image_utils.py     resolve image paths, existence check,
                          │                     deterministic duplicate/near-duplicate hashing
                          ├─ lookups.py         flat dict lookups: user_history.csv,
                          │                     evidence_requirements.csv
                          ├─ llm_client.py      builds the multimodal prompt + JSON schema,
                          │                     makes one structured Claude call per claim
                          │                     (prompts/system.md + prompts/examples.md)
                          └─ validation.py       deterministic assembly: claim_status cascade,
                                                 risk_flags clamping, severity/issue_type
                                                 consistency, valid_image, etc.
```

**The model judges, the code decides.** Claude Opus is trusted for genuinely visual judgment —
what's in the image, per-image quality/authenticity, severity, issue type, damage location — but
every field with a deterministic correctness rule (the `claim_status` cascade, severity/issue-type
consistency, flag pairings) is computed in `validation.py`, not trusted verbatim from the model's
JSON output.

Full design write-up, including why this is one LLM call per claim rather than several, in
[`code/README.md`](./code/README.md).

## Results

Scored against `dataset/sample_claims.csv`'s hand-labeled expected outputs
(see [`code/evaluation/evaluation_report.md`](./code/evaluation/evaluation_report.md) for the full
breakdown, including every bug this evaluation surfaced and fixed):

| Field | Accuracy |
|---|---|
| `claim_status` | 85% |
| `object_part` | 95% |
| `evidence_standard_met` | 90% |
| `valid_image` | 90% |
| `issue_type` | 70% |
| `supporting_image_ids` | 75% |
| `severity` | 55% |
| `risk_flags` | 55% |

Full test-set run (44 claims, 82 images): **~$1.76** at Claude Opus list pricing, with prompt
caching on the static system+examples prefix as the primary cost lever. Details, including the
rate-limit/retry/caching strategy and every production bug found and fixed along the way (an
oversized-image crash, a lost-work-on-crash bug, an authenticity-rule over-trigger, a
`claim_status` cascade-ordering bug), are in the evaluation report linked above.

## Quickstart

```bash
git clone https://github.com/zeyadelganainy/multimodal-claims-evidence-review.git
cd multimodal-claims-evidence-review
pip install -r code/requirements.txt
export ANTHROPIC_API_KEY=...        # never hardcoded; read from the environment only

# Full claim set -> ./output.csv
python code/main.py

# Score against dataset/sample_claims.csv's expected columns
python code/evaluation/main.py

# Browse results in a UI
streamlit run code/app.py
```

## Repository layout

```text
.
├── AGENTS.md                  # AI-agent build-process governance (logging, contract rules)
├── problem_statement.md       # Original hackathon brief and I/O schema
├── code/                      # The solution
│   ├── main.py                 # Pipeline entry point
│   ├── app.py                  # Streamlit results viewer
│   ├── llm_client.py            # Claude prompt construction + structured call
│   ├── validation.py            # Deterministic decision assembly
│   ├── image_utils.py           # Duplicate detection, image resolution
│   ├── lookups.py               # user_history.csv / evidence_requirements.csv lookups
│   ├── io_utils.py              # CSV read/write
│   ├── prompts/                # System prompt + worked examples
│   ├── _smoke_test_*.py        # No-API-call unit tests for each component
│   ├── README.md               # Solution architecture and design decisions (deep dive)
│   └── evaluation/
│       ├── main.py              # Scores predictions against sample_claims.csv
│       ├── evaluation_report.md # Accuracy results + operational analysis
│       └── EVALUATION_GUIDE.md  # Manual reviewer's step-by-step judgment checklist
├── dataset/                    # Claims, images, user history, evidence requirements
├── dev_notes/                   # Manual ground-truth verification artifacts (see its README)
└── output.csv                  # Final predictions for dataset/claims.csv
```

## License

MIT — see [`LICENSE`](./LICENSE).
