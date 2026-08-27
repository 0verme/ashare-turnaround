# Point-in-time financial adversarial contract

This document describes the synthetic version-chain contract enforced by
`tests/test_pit_adversarial.py`. It is intentionally separate from live-data
checks and never depends on a real token or on real NAS data.

## The future-function barrier

At as-of date `T`, a scan may only use financial information that was publicly
disclosed by `T`, **including corrected or revised disclosures**. The barrier
is enforced by disclosure/knowledge-time selection in `select_financial_as_of`,
not by canonicalization alone: `canonicalize_financial_frame` keeps every raw
version (so revisions are never silently lost), and only the as-of query
excludes versions whose `actual_available_date` is later than `T`.

`canonicalize != future-safe by itself`. A future-dated correction remains in
the canonical frame; the as-of query is what hides it from a past scan.

## Synthetic version chains

| Case | Scenario | Proven invariant |
| --- | --- | --- |
| A | First disclosure of a report period | The record is empty before `f_ann_date` and visible on/after it. |
| B | Original report then a later revised report (same identity) | The revised value is invisible before its own availability date and selected afterward; the original is still selected in between. |
| C | Same report period, same-day multiple versions (tie) | Exactly one version is selected per report identity; the higher `update_flag` wins on a same-day tie. |
| D | A correction disclosed in the future | The future correction is invisible at every past as-of date; it only applies once its own availability date is reached. |
| E | As-of boundary | Immediately before the first available date the result is empty; on the date it is visible; the revision boundary switches value on its exact date. |

## Cross-report-period

A report period whose `actual_available_date` is after `T` cannot satisfy a
query at `T`. A later annual report cannot be read at an earlier as-of date,
even if both are otherwise valid.

## Mapping evidence and refused fabrication

- Each canonical `PITMapping` records an `semantic_status` (`confirmed`,
  `suspected`, `unknown`).
- `income`/`balancesheet`/`cashflow`/`fina_indicator`/`forecast`/`express`/
  `fina_audit` are `confirmed` for their availability source.
- `fina_mainbz` and `disclosure_date.actual_date` remain semantically
  **unknown** for availability and are not silently upgraded; `fina_mainbz`
  requires an explicit `disclosure_date` join.
- A row with no usable availability date is **excluded** from an as-of query;
  the implementation never invents a date. `available_date_source` records
  where each selected date came from (`f_ann_date`, `ann_date`, or
  `disclosure_date.actual_date`).

## Cumulative quarterization

`derive_single_quarter` bridges cumulative `income`/`cashflow` values for the
standard quarter ends:

- Q1 = Q1
- H1 − Q1
- Q3 − H1
- FY − Q3

A missing prior cumulative observation stays missing rather than being
guessed (for example, a missing Q3 leaves FY's single-quarter value unknown).
Duplicate cumulative rows for the same `ts_code`/year/period raise instead of
silently deduplicating.

## Reproducibility

The `pit-check` report (`docs/pit-validation.md`) is reproducible from the
synthetic version chains plus the (possibly empty) bounded local store. It
states its limitations and raw-field provenance explicitly and never claims
unverified semantics for an API field.
