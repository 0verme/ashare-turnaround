# Feature ablation and stability contract

Feature ablation asks whether each cumulative feature group adds repeatable
evidence. It does not pick a production score from the best historical period.

## Frozen variants

`ScoreConfig.enabled_groups` makes feature groups independently switchable.
`ablation_score_configs()` declares four cumulative variants:

| Variant | Enabled groups |
| --- | --- |
| `fundamental_only` | fundamental, trend |
| `quality_added` | fundamental, trend, quality |
| `attention_added` | fundamental, trend, quality, attention |
| `expectation_added` | fundamental, trend, quality, attention, expectation |

Disabled groups retain their source inputs and component values for audit, but
receive zero weight. Their group-specific penalty and hard-gate effects are not
applied. Every variant has a distinct score configuration fingerprint.

Generate all variants from one raw input snapshot:

```bash
python -m ashare_turnaround replay-variants \
  --data-dir data \
  --as-of 20250630 \
  --top 20
```

The command refuses a run whose variants do not share one `snapshot_id`. Repeat
the command for the predeclared replay dates, then evaluate each variant with
the same benchmark, horizons, transaction costs, delisting rule, historical
membership, exposures, and PIT fundamental history.

## Stability report

Pass the four saved evaluation reports to `ablate`:

```bash
python -m ashare_turnaround ablate \
  fundamental_only=data/reports/evaluation-fundamental_only.json \
  quality_added=data/reports/evaluation-quality_added.json \
  attention_added=data/reports/evaluation-attention_added.json \
  expectation_added=data/reports/evaluation-expectation_added.json \
  --top 20 \
  --report data/reports/feature-stability.json
```

The analyzer rejects mismatched PIT snapshot sets and mismatched evaluation
configurations. It reports sample count, observed count, coverage, mean/median,
hit rate, dispersion, IQR, min/max, benchmark excess return, and Top-N rank
overlap. Results are segmented by year, benchmark-derived bull/bear/range
regime, market-cap bucket, industry, and holding horizon.

## Precommitted decision rule

The serialized `StabilityDecisionRule` requires minimum total and per-segment
observations, coverage, multiple years, regimes, and horizons, broad positive
segment share, positive median incremental return, and a bound on the worst
segment regression. It can classify a group as `stable_positive`, `redundant`,
`highly_regime_dependent`, `ineffective`, `unstable`, or
`insufficient_evidence`.

A single best segment is explicitly marked and is never enough to make
`promotion_eligible` true. Production defaults must be changed in a separate,
reviewed decision using out-of-sample or time-split evidence.
