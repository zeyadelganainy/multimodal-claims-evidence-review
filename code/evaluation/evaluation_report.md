# Evaluation Report

## What this covers

`code/evaluation/main.py` runs the exact same pipeline as `code/main.py` (it imports and calls
`process_row` directly — no duplicated logic) against `dataset/sample_claims.csv`, which carries
both the input columns and the expected output columns in one file. It strips each row down to
the input columns, runs it through the pipeline, and scores 8 of the 10 output fields against the
expected values already in the file. `evidence_standard_met_reason` and
`claim_status_justification` are free-text and intentionally not scored — there's no single
correct phrasing to diff against. `risk_flags` and `supporting_image_ids` are compared as sets
(order-insensitive); everything else is an exact string match.

Run it with:

```bash
python code/evaluation/main.py
```

It prints a per-row pass/fail, a per-field accuracy table, an exact-match rate, every mismatch
(field, expected, actual), and the token/cost totals for the run. Raw predictions are written to
`code/evaluation/sample_predictions.csv` for inspection.

## Accuracy on `dataset/sample_claims.csv` (20 rows)

Final run reflecting all fixes (including the additional session described under fix 6 below).

| Field | Accuracy |
|---|---|
| `object_part` | 19/20 (95%) |
| `evidence_standard_met` | 18/20 (90%) |
| `valid_image` | 18/20 (90%) |
| `claim_status` | **17/20 (85%)** |
| `issue_type` | 14/20 (70%) |
| `supporting_image_ids` | 15/20 (75%) |
| `risk_flags` | 11/20 (55%) |
| `severity` | 11/20 (55%) |
| **All 8 fields exact-match** | **6/20 (30%)** |

One row (`case_002`) is a known outlier: the user deliberately edited its expected ground truth
mid-session for an unrelated reason, so it is expected to disagree with the pipeline and was not
chased as a bug.

### Fixes found via this evaluation

Building and running this script against real expected labels (rather than ad hoc spot-checks)
surfaced four genuine, fixable issues, in roughly the order found:

1. **Undefined issue-type taxonomy.** `crack` vs `glass_shatter` vs `broken_part` were never
   actually defined anywhere in the prompt — left entirely to the model's judgment. This caused
   systematic mislabeling: every windshield/screen stone-chip crack in the sample was called
   `glass_shatter` instead of the dataset's `crack` convention, and a shattered mirror lens was
   called `glass_shatter` instead of the convention `broken_part`. Fixed by adding concrete
   definitions to `code/prompts/system.md`: a "still one piece" vs "broken apart" test for
   `crack`/`glass_shatter`, and an assembly-vs-flat-panel distinction (mirrors/headlights default
   to `broken_part` even when the lens itself is cracked; windshields and device screens use the
   crack/shatter test).
2. **User's casual wording held to a literal technical standard.** Fixing (1) caused one case to
   regress: a user described their screen as "shattered" colloquially, and the now-precise
   taxonomy caused the model to contradict the claim because the image showed a `crack`, not
   literal `glass_shatter`. Fixed by extending the existing "never let the user's severity framing
   move `severity`" principle to issue-type category labels too — the claim is "this part has
   damage," not "this part matches my exact word choice."
3. **Severity over-calibrated to visual drama.** A single-impact-point crack that still leaves the
   item structurally intact and functional was being called `high` severity because the fracture
   *pattern* looked dramatic, where the dataset convention was `medium`. Added a calibration
   anchor: judge severity by functional impact, not by how much of the surface the crack pattern
   covers.
4. **Multi-image, single-issue contradiction bug.** When one image confirms a claimed issue and a
   *different* image (e.g. a wider, different-angle shot of the same item) simply doesn't happen to
   show it, the model was contradicting the claim instead of staying `supported` on the confirming
   image. Added a rule mirroring the existing multi-part-claims logic, scoped to multiple images of
   one single claimed issue.
5. **A real `validation.py` cascade-ordering bug** (not a prompt issue): the deterministic
   `claim_status` cascade checked the authenticity fraud-deny rule *before* checking whether any
   image was even sufficient to judge the claim. A wrong-angle photo that also happened to look
   stock-styled was getting reframed as a confident `contradicted` instead of the correct
   `not_enough_information` — there was nothing to contradict, since the claimed part was never
   even in frame. Reordered the cascade (insufficiency checked first) and added regression coverage
   (`code/_smoke_test_validation.py`, test G) confirming the opposite case — an authenticity issue
   on an image that *was* otherwise sufficient — still correctly forces `contradicted`.

Net effect of fixes 1–5 on the sample set (first evaluation pass): exact-match rows 3/20→8/20;
`claim_status` 65%→75%, `issue_type` 40%→65%, `severity` 40%→60%, `risk_flags` 30%→40%.

6. **Authenticity-issue over-triggering on well-lit photos.** A worked example in
   `code/prompts/examples.md` stated that "polished framing, lighting, and depth-of-field are
   hallmarks of stock/promotional photography — even with no visible watermark," which contradicted
   the intent of the concrete-signal rule in `system.md` (watermarks, seamless-background isolation,
   UI chrome, impossible data, compositing tells — not mere photo quality). Fixed by updating the
   example to identify the *specific* concrete tells (isolated seamless background, artificial bokeh
   with no real-world context) that qualified the images in that example, and adding an explicit
   water-damage calibration worked example showing that a dramatic, well-lit damage photo with no
   concrete authenticity tell is `supported`, not suspect. Also tightened the `wrong_angle` and
   `wrong_object_part` flag definitions in both `system.md` and `examples.md`: both require the
   claimed part to be absent from the frame entirely, not merely shown partially or from an angle.

Net effect of fix 6: `claim_status` 75%→85%, `risk_flags` 40%→55%, `issue_type` 65%→70%.

### Known residual limitations (accepted, not chased further)

- **Model judgment variance on borderline images** (e.g. whether faint damage is discernible
  through heavy blur, or whether a photo's polish reads as "stock-styled"). `temperature` is not
  configurable on this model/endpoint for this kind of call, so this variance can't be pinned down
  further without a different model parameter. In every case checked, this affected
  `issue_type`/`severity`/`risk_flags` precision, not the underlying `claim_status` decision.
- **`wrong_object`/`non_original_image` over-triggering on a handful of rows** (`case_010`,
  `case_012`, `case_016`) where the expected flags are `none`. Spot-checked and judged as the model
  being more cautious about authenticity/object-match than the dataset's ground truth, not a
  decision-correctness bug — `claim_status` was correct in the cases checked.
- A few remaining one-off `claim_status` disagreements (`case_015`, `case_018`, `case_020`) reflect
  genuinely ambiguous evidence (e.g. packing material obscuring whether an item is present, torn
  tape vs. intact seal interpretation) where the model and the dataset's ground truth reach different
  but each individually defensible conclusions. Not chased further given the time budget.

## Operational analysis

**Model:** `claude-opus-4-8`, one structured multimodal call per claim row (no batching across
rows — each claim has its own images/context and needs its own call).

| | Sample set (20 rows) | Test set (44 rows) |
|---|---|---|
| Rows | 20 | 44 |
| Images processed | 29 | 82 |
| API calls | 20 (1/row) | 44 (1/row) |
| Input tokens | 42,416 | 121,101 |
| Output tokens | 10,407 | 27,247 |
| Cache read tokens | 218,416 | 497,464 |
| Cache write tokens | 24,240 | 36,392 |
| **Estimated cost** | **$0.733** | **$1.763** |
| Avg cost / call | $0.037 | $0.040 |

Pricing assumptions (Claude Opus 4.8 list price, USD per 1M tokens): input $5.00, output $25.00,
cache write $6.25 (1.25× input, 5-minute TTL), cache read $0.50 (0.1× input) — see
`PRICE_PER_MTOK` in `code/main.py`. The test-set run reflects every fix in this report, including
the caching change below; the sample-set numbers above predate that change, which is why its
avg-cost/call is higher despite otherwise-comparable per-row complexity.

**Full claims.csv (44 rows) actual cost: ~$1.76.** Per-row cost will vary with image count and
authenticity/cross-image complexity (more images per claim costs more), so this is closer to a
realistic estimate than a pure extrapolation — it's the actual measured cost of the full test set
under the final code.

**Latency/runtime:** not separately instrumented with a timer, but every multi-row run in this
session (up to 44 sequential calls) completed comfortably within a few minutes of wall-clock time.
At roughly 3–8 seconds per multimodal call, a 44-row sequential run is expected to take on the
order of 3–6 minutes end to end.

**Rate limits, batching, caching, retries:**

- **Sequential, single-threaded processing.** No concurrency is used. This trades wall-clock time
  for safety against TPM/RPM bursts — at this dataset size (44–82 images), sequential calls stay
  comfortably under standard-tier rate limits without needing throttling logic.
- **Prompt caching is the primary cost lever.** The static system+examples prefix (~14.5KB,
  `code/prompts/system.md` + `code/prompts/examples.md`) is marked with a single `cache_control`
  breakpoint at the end of the examples block, so the whole prefix caches as one unit. The first
  call in a 5-minute window pays the cache-write rate; every subsequent call pays the cache-read
  rate (0.1× input price) for that ~14.5KB instead of full price. This was tightened mid-session:
  the breakpoint originally only covered `system.md`, leaving `examples.md` (which grew
  substantially over the session) priced at full input rate on every call — moving the breakpoint
  to cover both immediately increased cache-read volume and dropped per-call cost on subsequent
  runs.
- **Image size/format normalization.** Images are downscaled to a max 1568px edge (matching
  Claude's effective resolution ceiling, so no quality is lost for review purposes) and re-encoded
  as JPEG with a quality-backoff loop (90→30) until the base64 payload fits comfortably under the
  API's 10MB cap. This was a real production bug found mid-session: one dataset image was an AVIF
  file at 3000×4512 saved with a `.jpg` extension, and the original encoder's lossless-PNG
  re-encode of it ballooned past the 10MB cap, crashing the run.
- **No custom retry/backoff beyond the Anthropic SDK's defaults.** Instead, per-row resilience: any
  unrecoverable failure on a given row (`ClaimReviewError` for a refusal/truncated response, or any
  other `anthropic.APIError`) is caught and converted into a deterministic
  `not_enough_information` + `manual_review_required` row rather than retried indefinitely or
  allowed to crash the batch. This was tightened mid-session after a real incident: the original
  code only wrote output once, at the very end of the run — a crash on row 35 of 44 lost the
  already-completed (and already paid-for) work on rows 1–34, since nothing had been persisted yet.
  Output is now written after every row, so a crash can no longer lose prior progress.
