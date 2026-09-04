# Historical PIT replay validation summary

> Final execution status for Issue #32. This is a correctness-validation sample,
> not a performance backtest. Large RAW/replay artifacts remain local and
> gitignored; the detailed closure audit is in
> [issue32-pit-replay-validation-closure.md](issue32-pit-replay-validation-closure.md).

## Final decision

```text
ISSUE32_READY_TO_CLOSE
```

- Contract: `pit-replay-validation-v1`
- Monthly target rule: `monthly-anchor-15-v1`
- Representative sample rule: `representative-regime-strata-v1`
- Validation cutoff: `20260830`
- Resource gate: `resource-gate-v3`
- Input manifest: `de86753eeedb250b8fd8967ae285707e6040602684cae3ce533292c0df69290f`
- Schedule digest: `9869849b0a22a5e64b482677b4cceb1315c027f8222ec842b4af52cd4c310bf8`

## Layer 1 schedule

The inclusive `2017-01..2026-12` schedule contains 120 targets:

```text
AVAILABLE=108
UNAVAILABLE_DATA=7
INCOMPLETE_CURRENT_MONTH=1
UNAVAILABLE_FUTURE=4
```

The fixed anchor is the 15th, followed by the first same-month open SSE session.
There is no neighboring-month, future, or current-universe substitution.

## Layer 2 execution

All ten non-2025-06 frozen members were already present as complete passing
single-target production replays. They were validated and reused; no duplicate
aggregate replay was launched. The retained `2025-06 / 20250616` resource-gate-v3
baseline/repeat was reused as `EXISTING_VALIDATED / DO_NOT_RERUN`.

| Month | Date | Regime | Status | Candidates | Top-N | PIT | Resource |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2017-01 | 20170116 | range | READY | 2,720 | 3 | 0 | PASS_WITH_WARNING |
| 2018-10 | 20181015 | bear | READY | 3,453 | 3 | 0 | PASS_WITH_WARNING |
| 2019-03 | 20190315 | bull | READY | 3,543 | 3 | 0 | PASS_WITH_WARNING |
| 2020-01 | 20200115 | range | READY | 3,663 | 3 | 0 | PASS_WITH_WARNING |
| 2020-09 | 20200915 | bull | READY | 3,815 | 3 | 0 | PASS_WITH_WARNING |
| 2022-05 | 20220516 | bear | READY | 4,559 | 3 | 0 | PASS_WITH_WARNING |
| 2023-12 | 20231215 | bear | READY | 5,038 | 3 | 0 | PASS_WITH_WARNING |
| 2024-05 | 20240515 | range | READY | 5,072 | 3 | 0 | PASS_WITH_WARNING |
| 2024-11 | 20241115 | bull | READY | 5,069 | 3 | 0 | PASS_WITH_WARNING |
| 2025-06 | 20250616 | range | EXISTING_VALIDATED | 5,102 | 3 | 0 | PASS |
| 2025-12 | 20251215 | range | READY | 5,134 | 3 | 0 | PASS_WITH_WARNING |

All ten new artifacts pass `gzip -t`, streaming JSON syntax validation, and
SHA-256 verification. Their checkpoints are `COMPLETE`; all machine audits are
`PASS`; no hard resource failure, allocator failure, or PIT violation occurred.
The 2025-06 pair is byte-identical at 2,781,058,369 bytes with SHA-256
`142082b0649180e09e0dea946feb868f6e831d314c39324c2e69a37a154adce8` and has
semantic/artifact/determinism audit status `PASS`.

## Manual review

The frozen manual subset is complete using agent-assisted review without UI,
network secrets, or future outcomes:

- `2019-03` bull: Top-3 `600817.SH`, `000543.SZ`, `000055.SZ`; diagnostic
  ineligible `002940.SZ`; unknown-heavy `600421.SH`.
- `2022-05` bear: Top-3 `300343.SZ`, `002759.SZ`, `000792.SZ`; diagnostic
  ineligible `002255.SZ`; unknown-heavy `688192.SH`.
- `2025-06` range: Top-3 `688233.SH`, `002355.SZ`, `688615.SH`; diagnostic
  ineligible `688286.SH`; unknown-heavy `688302.SH`.

Each review also covers `000003.SZ` excluded as `delisted_by_as_of`, formal
ranking eligibility, feature-group evidence/coverage/confidence, score
breakdown, benchmark `000300.SH`, financial availability/PIT boundaries,
unknown groups, and the unsupported historical stock_basic fields. Unknowns are
not neutral-filled and ineligible diagnostic rows do not enter formal Top-N.

## Validation and scope guard

- `ruff check .`: PASS
- `python3 -m compileall -q src tests`: PASS
- `PYTHONPATH=src pytest -q`: 302 passed, 1 skipped
- Synthetic adversarial fixtures: 9/9 PASS
- PIT violations: 0
- Resource hard failures: 0
- RAW rewrite/redownload: not performed
- Evaluation #17: not run
- Ablation/Stability #18: not run
- Forward-return analysis, holding-period/profit-target optimization, score or
  threshold tuning, parameter search, and Score v2 changes: not performed

## Limitations

Historical `stock_basic` name/status/industry/board state remains
`UNSUPPORTED_PIT`; no current snapshot fallback is used. Some sample runs carry
only declared resource-gate-v3 soft swap warnings and therefore report
`PASS_WITH_WARNING`. A historical 2020-09 campaign log records a transient
external-harvest metadata drift; the final replay-relevant postflight and
artifact audit are unchanged/PASS, and the original evidence is retained.
