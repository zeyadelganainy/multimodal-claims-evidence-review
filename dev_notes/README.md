# Dev notes

Working artifacts from manual ground-truth verification during development, kept for
transparency rather than deleted.

- **`dev_shortlist_12.csv`** — a 12-row shortlist used to spot-check the pipeline against
  hand-reviewed cases. **Only `case_001`, `case_006`, `case_007`, `case_008`, and `case_017`
  are genuinely human-verified** — each was walked through manually, image by image, with
  several judgment calls corrected in the process (e.g. a cross-image vehicle mismatch in
  `case_008` that the model initially missed). The other 7 rows in this file are an earlier,
  unverified LLM-judgment shortlist and were explicitly **not** treated as ground truth in any
  accuracy claim made in `code/evaluation/evaluation_report.md`.
- **`dev_shortlist_output.csv`** — pipeline output for the shortlist above, from the same run
  used for the spot-checks.
- **`dev_smoke_3.csv`** — a 3-row subset of `dataset/claims.csv` used for fast manual iteration
  during prompt/validation changes, without paying for a full 44-row run each time.
