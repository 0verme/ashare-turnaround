# Comparable Period Semantics

This document defines the v1 contract used by financial feature derivation.  It
is intentionally fail-closed: a period, version, unit, scope, or availability
that cannot be proven is `UNKNOWN`, not an estimated number.

The contract version is:

```text
comparable_period_contract_version = "comparable-period-v1"
```

The actual feature call chain is:

```text
RAW financial frame
    ↓
financial canonicalization (period identity + PIT availability)
    ↓
existing as-of disclosure selection
    ↓
comparable-period matcher
    ↓
validated cumulative quarterization (when required)
    ↓
validated growth / margin / TTM primitive
    ↓
fundamental feature evidence + downstream metadata
```

## Canonical period identity

Every canonical financial row carries:

- `report_period`: normalized report end date;
- `fiscal_year`, `fiscal_period`, and `quarter`: the fiscal year and standard
  quarter endpoint (`Q1`, `H1`, `Q3`, or `FY`; quarter is 1–4);
- `report_family`: the source report family, including the report type where
  available;
- `statement_type`: `INCOME_STATEMENT`, `CASH_FLOW_STATEMENT`, or
  `BALANCE_SHEET` (among other explicitly named source types);
- `duration_semantics`: one of `SINGLE_QUARTER`, `CUMULATIVE_YTD`,
  `POINT_IN_TIME`, or `UNKNOWN`;
- `scope`: for example `consolidated` or `parent_only`;
- `unit` and `accounting_semantics`;
- `source_version_identity` / `source_version`,
  `actual_available_date`, and `comparable_period_contract_version`.

An income statement `2025-06-30` cumulative observation and an income
statement `2025-06-30` single-quarter observation therefore have different
identities even though `report_period` is the same.  A balance sheet is
`POINT_IN_TIME`; income-statement quarterization is never applied to it.

The standard Tushare financial statement adapter maps the validated report
families as follows:

- consolidated / parent report families remain separate;
- cumulative report families are `CUMULATIVE_YTD`;
- source single-quarter report families are `SINGLE_QUARTER`;
- standard balance-sheet rows are `POINT_IN_TIME`.

Non-standard endpoints, conflicting semantic fields, and unrecognized period
ends remain `UNKNOWN`.

## Point-in-time selection

Canonicalization retains every raw disclosure version.  The existing PIT engine
then selects, for each report identity, the latest version satisfying:

```text
actual_available_date <= as_of_date
```

For example:

```text
original: value=100, available=2025-04-20
revision: value=120, available=2025-05-10
```

At `as_of=2025-05-01` only `100` is visible.  At `as_of=2025-05-10` the value
is `120`.  The later revision cannot alter an earlier feature.  A missing or
unproven availability date is excluded from PIT selection; no date is
fabricated.

## YoY

YoY uses a period matcher, never the previous row:

```text
2025Q3 vs 2024Q3 = YoY ✅
2025Q3 vs 2025H1 = YoY ❌
```

The matcher requires the same fiscal quarter, duration semantics, report
family, statement type, scope, unit, and accounting semantics.  Consequently,
these are valid:

```text
2025Q1 single       vs 2024Q1 single       ✅
2025Q2 single       vs 2024Q2 single       ✅
2025Q3 cumulative   vs 2024Q3 cumulative   ✅
2025FY cumulative   vs 2024FY cumulative   ✅
2025Q3 single       vs 2024Q3 cumulative   ❌
```

An adjacent disclosure with a different economic period is rejected with a
reason such as `missing_comparable_period` or
`period_semantics_mismatch`.

## Single quarter and quarterization

For cumulative income statement or cash-flow observations, a single quarter is
available only after the source chain is validated:

```text
Q1 single = Q1 cumulative
Q2 single = H1 cumulative - Q1 cumulative
Q3 single = Q3 cumulative - H1 cumulative
Q4 single = FY cumulative - Q3 cumulative
```

Subtraction requires the same fiscal year, report family, statement type,
scope, unit, accounting semantics, and a PIT-visible predecessor.  A missing
predecessor, unit/scope mismatch, duplicate ambiguous chain, or future
predecessor returns `UNKNOWN` with a reason such as:

```text
missing_preceding_cumulative_period
unit_mismatch
scope_mismatch
ambiguous_period_chain
future_disclosure_not_visible
insufficient_evidence
```

The result carries the complete source period, version, and availability chain.
Balance-sheet rows are not quarterized.

## QoQ

For income and cash-flow, QoQ is matched only on a validated
`SINGLE_QUARTER` series.  Thus `2025Q3 single vs 2025Q2 single` is valid, but
`2025Q3 cumulative vs 2025H1 cumulative` is not QoQ.  A QoQ result preserves
both period identities, raw/comparable values, quarterization provenance,
source versions, and availability dates.

## TTM

TTM is the sum of four consecutive validated single-quarter observations:

```text
TTM ending 2025Q3
= 2024Q4 single + 2025Q1 single + 2025Q2 single + 2025Q3 single
```

The result records `ttm_end_period`, all `source_quarters`, source versions,
and availability dates.  A missing, ambiguous, mixed-scope, mixed-unit,
unsupported, or future quarter returns `UNKNOWN`; incomplete cumulative chains
are never used to guess a TTM.

## Margin comparison

A margin is calculated inside each comparable period first:

```text
margin_t = numerator_t / revenue_t
margin_yoy_change = margin_t - margin_t-1y
```

The implementation does not use `(delta numerator) / (delta revenue)` as a
margin change.  Scoped v1 margins are gross, operating, and net margin where
the source fields exist.  Zero revenue produces `UNKNOWN` with
`invalid_denominator`; negative revenue is not treated as an ordinary margin
base.

## Growth denominator policy

Ordinary growth rates require a strictly positive comparison denominator.

- zero base: `UNKNOWN`, reason `invalid_denominator`;
- negative base: `UNKNOWN`, reason `negative_denominator`;
- `negative → positive` and `positive → negative` are also represented as a
  separate `sign_transition` provenance value and are not ranked as ordinary
  growth (the positive-to-negative result is `UNKNOWN` with reason
  `sign_transition`);
- zero transitions are represented as a separate `sign_transition` provenance value;
- no infinity, clipping, or arbitrary replacement is emitted.

For example, `-1 → +1` is not emitted as an ordinary `+200%` growth rate.

## Provenance and downstream boundary

Every derived feature evidence record includes the metric, status, reason,
current/comparison period, raw values, period semantics, datasets and fields,
availability dates, source versions, source chain, and
`contract_version`.  `FeatureVector` and replay metadata expose the same
comparable-period contract version.  The downstream trend layer is separately
versioned as `turnaround-trend-v2`; the two versions are never substituted for
one another.  See [Trend Semantics](trend-semantics.md) for the level/change/
acceleration/persistence contract.

The comparable-period contract remains the only source of period semantics.
Trend consumers must fail closed when its validated primitives are absent or
invalid.  No score weights are changed by either contract.
