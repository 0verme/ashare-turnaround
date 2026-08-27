# Low Attention v2 — cross-sectional context for attention proxies (issue #29)

**Semantic version:** `low-attention-v2.0.0`
**Status:** research calibration of issue #13. Does **not** alter the
production Turnaround Score v1 (the v1 `attention_score` remains the only
attention component consumed by `score.py`).

Core principle:

> **低关注 ≠ 低流动性；数据缺失 ≠ 低关注。**
> Low attention is not low liquidity, and missing data is not low attention.

---

## 1. v1 audit and root cause (#13)

v1 lives in `features/market.py::compute_attention_features`. It produces
`turnover_percentile`, `amount_percentile`, `abnormal_volume` and a blended
`attention_score`. The audit found:

| # | Finding | Consequence |
| --- | --- | --- |
| 1 | The percentile reference population is **self-history only**, and the **current observation is included in its own baseline** (`(clean <= current).mean()` over a window that contains the current row). | No cross-sectional context; the current session contaminates its own rank; the minimum achievable percentile is `1/N`, not `0`. |
| 2 | Missing proxies are **silently imputed**: `turnover or 0.5`, `amount or 0.5`, `abnormal or 1.0`. | A symbol with missing turnover is treated as "average attention" instead of "unknown" — missing data becomes evidence. |
| 3 | Suspension/staleness are not detected; the "latest" row is silently the last traded day. | A suspended symbol can look like a stable low-activity name. |
| 4 | `attention_score` rises when activity percentiles fall (`1 - p` terms). | Extreme inactivity mechanically produces a high opportunity score. v1 has **no liquidity gate of its own**, and `UniverseConfig.min_average_amount` defaults to `0.0` (floor off). In the repro, an inactive fixture scores ≈ 92 "opportunity". |
| 5 | No per-proxy evidence beyond datasets/fields/periods. | No observation date, population size, valid counts, window, or version is queryable per proxy. |
| 6 | New listings with as little as 1 session produce a "percentile". | Single-row self-percentile is self-referential (`1.0`). |

**Root cause in one sentence:** v1 collapses *self-history*, *cross-sectional
context*, *liquidity eligibility* and *missing evidence* into one blended
number, which makes inactivity and missingness indistinguishable from "low
attention".

---

## 2. Low Attention v2 contract

Two explicit dimensions plus an independent eligibility contract. The three are
never merged into one `attention` field:

```text
self-history        percentile(x_t against prior N valid sessions of same symbol)
cross-sectional     percentile(x_{symbol,t} across declared population at t)
liquidity           separate eligibility verdict (amount floor, session, listing age)
```

Implemented proxies (all derived from existing `daily` + `daily_basic`, no new
data source):

| Name | Kind | Definition |
| --- | --- | --- |
| `self_turnover_percentile` | self | `P(prior_window_turnover <= current_turnover)`, prior window only |
| `self_amount_percentile` | self | same on `amount` |
| `self_volume_percentile` | self | same on `vol` |
| `cross_section_turnover_percentile` | cross | `P(population_turnover_t <= current)` inclusive, population at session t |
| `cross_section_amount_percentile` | cross | same on `amount` |
| `cross_section_volume_percentile` | cross | same on `vol` |
| `abnormal_volume` | prior baseline | `current_vol / median(prior 60-session vol)`, capped at 10× with a flag |
| `attention_baseline_change` | prior baseline | `current_turnover / median(prior 60-session turnover)` — "attention fading" ratio |
| `session_status` | session | `traded` / `suspended_session` / `stale` / `no_data` (+ `staleness_days`) |
| `liquidity_eligible` | eligibility | independent research gate (see §7) |
| `low_attention_v2_score` | research aggregate | mean of `(1 - p)` over the four core percentiles; **research-only**, never feeds the production score, and must be gated by liquidity eligibility before any opportunity reading |

### Self-history

`percentile(x_t against prior N valid sessions of the same symbol)`:

- the current session is excluded from its own baseline: baseline rows are
  strictly prior (`trade_date <` the current session), sliced to the last
  `window` sessions;
- `window = 252` sessions (one trading year; configurable in
  `SelfWindowConfig`, `version: self-window-v1`);
- `min_valid = 21` prior observations required, otherwise `unknown` with
  reason `insufficient_self_history`;
- new listing (listing age below `min_listing_days = 120`, supplied via
  `list_date`): `unknown` with reason `new_listing` — a warm-up history is not
  a baseline;
- no current observation: `unknown` with reason `missing_current_field`;
- no rows at all: `unknown` with reason `insufficient_self_history`;
- future data is impossible by construction: rows are filtered to
  `trade_date <= as_of` (and `actual_available_date <= as_of` when present).

### Cross-sectional

`percentile(x_{symbol,t} across declared population at t)`:

- the population is anchored at the **effective session** `t` = the most recent
  market trading session at or before `as_of` (derived from the data, so a
  weekend/holiday `as_of` is not mistaken for a suspension);
- **population scope (choice with rationale):**
  `tradable_market` (default) — every symbol with an observed value at session
  `t`. Chosen because (a) it is fully derivable from the existing data with no
  ordering dependency on the universe builder; (b) a liquidity-based investable
  universe would be circular here (percentiles computed only among other
  investable names inflate a low-liquidity name's rank); (c) eligibility is a
  separate gate anyway. `investable_universe` is available for research by
  passing the as-of universe code list (`investable_codes`).
- ties: inclusive convention `P(X <= x)`, fixed and deterministic — identical
  values receive identical percentiles; no fractional tie-breaking;
- population minimum: `min_population = 20` valid values, otherwise `unknown`
  with reason `insufficient_population`;
- the symbol must have an observation at `t`; otherwise `unknown` with reason
  `no_observation_at_session` (suspension);
- population count and scope are stored in the evidence of every cross-section
  proxy.

### Trading-session semantics

- `as_of` is a *decision* timestamp, not necessarily a trading session;
- effective session = `max(trade_date in market frame, <= as_of)`;
- a symbol is `traded` if its latest observation is at the effective session;
- a symbol whose latest observation is earlier but within
  `max_staleness_days = 10` calendar days is `suspended_session`;
- a larger lag is `stale` (explicitly distinct from suspension);
- `suspended_session`/`stale` symbols: cross-section proxies and
  current-session ratios are `unknown`; the eligibility verdict is `False`
  (reason `no_session_at_decision`).

### Abnormal volume and attention baseline change

- baseline = median of the **prior** `baseline_window = 60` sessions
  (current excluded), `min_observations = 20`;
- zero baseline → `unknown` (reason `zero_baseline`);
- extreme outliers are capped at `max_abnormal_cap = 10.0` and flagged
  (`risk_flags += abnormal_volume_capped`);
- missing current value → `unknown`; missing/stale → `unknown` with the
  session reason.

### Missing / stale policy

Missing is a first-class state, never a low value:

- `missing_current_field` — field absent at the session;
- `insufficient_self_history` — baseline too short;
- `insufficient_population` — cross-sectional population too small;
- `zero_baseline` — median baseline is zero (no divide-by-zero fabrication);
- `new_listing` — listing-age policy; `suspended_session` / `stale_data` —
  session policy.

---

## 3. Liquidity vs attention boundary (issue #29 anti-bypass)

The **composite never converts inactivity into an opportunity**:

1. `liquidity_eligible` is computed by an independent gate
   (`assess_liquidity_eligibility`, `liquidity-gate-v2`): trailing 20-session
   average `amount` ≥ research floor `1.0`, current-session trading, listing
   age ≥ `min_listing_days`, and explicit reasons for every failure.
2. Sample classification (`classify_low_attention_case`) is ordered:
   policy/session exclusion (new listing, suspension, staleness) **first** →
   `B`; then missing data → `C`; then liquidity ineligible → `B`; only an
   eligible symbol with known proxies can be `A`.
3. The production gate remains `build_investable_universe` with an explicit
   `min_average_amount` floor (`UniverseConfig.min_average_amount`); v2 never
   bypasses it, and the fixture proves the floor excludes the extreme-illiquid
   name (`low_liquidity`).

> **Residual risk note:** v1's `attention_score` still composes `1 - percentile`
> terms without an internal liquidity stop, and the universe liquidity floor
> defaults to `0.0` (off). v2 does not change that production default (out of
> scope); the v2 contract itself is closed against the bypass, and
> `docs/scanner-contracts.md` is unchanged on this point. Enabling
> `min_average_amount > 0` in the production `ReplayConfig` remains the
> recommended production-side mitigation.

---

## 4. Optional external proxies — exclusions for v2

Issue #29 mentions shareholder count, institutional ownership, northbound,
margin and analyst coverage. **None are wired into v2**:

| Proxy | Decision | Reason |
| --- | --- | --- |
| shareholder count | excluded for v2 | source reliability not proven; PIT timing unknown |
| institutional ownership | excluded for v2 | historical coverage not proven; disclosure lag varies |
| northbound holding | excluded for v2 | publication timing not proven; PIT availability unverified |
| margin (融资融券) | excluded for v2 | coverage/timing not proven for the full A-share market |
| analyst coverage | excluded for v2 | no reliable historical feed; timestamps not PIT-verifiable |

Principle: a proxy enters v2 only when source reliability, historical
coverage, publication timing and PIT availability are demonstrated. Pseudo-
precise data is worse than an explicit exclusion.

---

## 5. Evidence schema

Every proxy carries a `FeatureEvidence` entry whose `metadata` includes:

```text
kind                       self | cross_sectional | prior_baseline
as_of_date                 decision date (YYYYMMDD)
observation_date           session of the raw observation (YYYYMMDD)
raw_current_value          raw observed value at the session
percentile                 computed percentile (None for ratios)
valid_observation_count    valid baseline observations used
population_count           population size at the session (cross-section)
window                     baseline window used
source                     dataset provenance
semantic_version           low-attention-v2.0.0
warnings                   missing / stale / suspension / cap reason
population_scope           tradable_market | investable_universe   (cross)
tie_convention             inclusive                              (cross)
baseline_median_*          ratio baselines                         (abnormal)
capped                     outlier cap applied                     (abnormal)
```

The output is a `FeatureVector` with `version = "low-attention-v2.0.0"`; v2
field names are namespaced (`self_*`, `cross_section_*`, ...) and never
overwrite v1 fields. `score_feature_vector` continues to see
`attention_score = None` for v2 vectors — the v1/v2 boundary is explicit.

---

## 6. Sample report (research artifact, no trading recommendations)

Generated by `low_attention_sample_report()` /
`low_attention_sample_report_markdown()` from the synthetic market fixture
(34 symbols × 390 sessions ending 2025-06-30; `daily.vol` plus
`daily_basic.turnover_rate/amount`). Buckets:

- **A** — genuinely low attention, investable;
- **B** — extreme inactivity / not liquidity eligible ⇒ NOT an opportunity;
- **C** — attention evidence missing ⇒ attention `unknown`.

```text
 ts_code   class                     reasons                         session_status  liquidity_eligible  liquidity_average_amount  self_turnover_percentile  self_amount_percentile  cross_section_turnover_percentile  cross_section_amount_percentile  cross_population_count  abnormal_volume  attention_baseline_change  low_attention_v2_score
600000.SH  A_eligible_low_attention  low_attention_observed         traded          True                16964.0                    0.0                       0.0                     0.0606                              0.0606                           33                      0.9437            0.9010                    96.97
600002.SH  C_attention_unknown       insufficient_attention_evidence traded          False              NaN                        NaN                      NaN                    NaN                                 NaN                              33                      1.0000            NaN                      NaN
600001.SH  B_not_liquidity_eligible  low_liquidity                   traded          False              0.02                      1.0                       1.0                     0.0303                              0.0303                           33                      1.0000            1.0000                    48.48
```

Remarks:

- `600000.SH` is a genuine low-attention **and investable** case (cross-
  sectional ~0.06 for both turnover and amount, average amount way above the
  floor) → **A**;
- `600001.SH` is the extreme-illiquid garbage name (average amount `0.02`,
  below the explicit floor). Its raw proxies are the most extreme in the
  fixture and its naive aggregate would read "low attention", yet it is
  classified **B**, never **A** — inactivity does not create an opportunity;
- `600002.SH` has no turnover/amount observations at all → attention is
  **unknown** → **C**, never "low attention".

No trading recommendation is produced; the markdown renderer states the
research-only nature explicitly.

---

## 7. Files

| File | Purpose |
| --- | --- |
| `src/ashare_turnaround/features/low_attention.py` | v2 contract: configs, self/cross/baseline proxies, eligibility, classification, sample report |
| `src/ashare_turnaround/features/__init__.py` | exports (`compute_low_attention_v2`, sample report helpers) |
| `src/ashare_turnaround/scanner/contracts.py` | `FeatureEvidence.metadata` (additive, backward compatible) |
| `src/ashare_turnaround/features/common.py` | `add_known(..., metadata=...)` passthrough (additive) |
| `tests/test_low_attention_v2.py` | 23 contract tests (see §8) |
| `docs/low-attention-v2.md` | this document |

## 8. Test coverage

rolling percentile boundary; current-session exclusion; insufficient history;
new listing; suspended stock; stale data; missing observations; cross-sectional
ties; deterministic tie handling; historical (as-of) universe state; PIT
cutoff (future rows ignored); abnormal-volume prior baseline; extreme outlier
cap+flag; zero baseline; extremely illiquid false opportunity (A/B/C);
v1/v2 version boundary; evidence completeness; deterministic output; no-trade-
recommendation report. Full suite: 118 passed (95 pre-existing + 23 new),
ruff clean.