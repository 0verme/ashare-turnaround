# Phase 2.5 closure matrix

Read-only acceptance audit completed before baseline outcome observation.

| Work item | Versioned contract | Tests / fixtures | PIT rule | Missing-data policy | Status |
| --- | --- | --- | --- | --- | --- |
| #27 Comparable financial periods | `comparable-period-v1` / `docs/comparable-period-semantics.md` | `tests/test_comparable_period_semantics.py`, revised/chain/unit fixtures | `actual_available_date <= as_of` | fail closed as `unknown` with period/denominator reason | PASS |
| #28 Trend / acceleration | `turnaround-trend-v2` / `docs/trend-semantics.md` | `tests/test_trend_semantics.py`, sign/persistence/revision fixtures | inherited #27 PIT chain | insufficient/discontinuous history is `unknown` | PASS |
| #29 Low Attention v2 | `low-attention-v2.0.0` / `docs/low-attention-v2.md` | `tests/test_low_attention_v2.py`, suspension/stale/tie/adversarial fixtures | session and publication cutoff at as-of | missing is never low attention; explicit unknown | PASS |
| #30 Expectation / Crowding v2 | `expectation-crowding-v2` + `benchmark-v1` | `tests/test_crowding_v2.py`, missing/misaligned benchmark fixtures | market/reference observations `<= as_of` | no benchmark fallback; explicit unknown | PASS |
| #31 Evidence / confidence | `evidence-confidence-v1` / `docs/evidence-confidence-v1.md` | `tests/test_evidence_confidence.py`, critical-group gate fixtures | PIT warnings fail closed for coverage | field/group coverage, confidence, unknown groups, eligibility are separate | PASS |
| #32 Historical PIT replay | `pit-replay-validation-v1` / closure docs | `tests/test_replay_validation.py`, 9/9 adversarial fixtures, manual review | financial/market/reference bounded at snapshot | unavailable months and incomplete inputs remain explicit | PASS |

## Gate decision

```text
PHASE2_5_READY_TO_CLOSE
```

Issue #32 is formally closed. Issue #26 was closed after the audit. The audit
found no correctness gap and did not change Scanner formulas, Score weights,
thresholds, or historical data.
