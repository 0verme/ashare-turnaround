# Market / Reference historical corpus coverage

- Generated at (UTC): `2026-08-27T19:01:12.214656+00:00`
- Data directory: `/vol5/1000/ai-workspace/repos/ashare-turnaround/data`
- Research window: `20120101..20251231`
- Main benchmark: `000300.SH`
- Verdict: **`READY`**
- This report performs coverage/integrity checks only; it does not run scanner, replay, evaluation, ablation, or candidate reporting.

## Dataset completion matrix

| Dataset | Expected range | Actual range | Expected units | PASS units | Rows | Size | Missing trading days | Status |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| trade_cal | 20120101..20251231 | 20120101..20251231 | 2 | 2 | 10,228 | 58.25 KiB | 0 | COMPLETE |
| stock_basic | snapshot@20260827 | 20260827 | 1 | 1 | 5,894 | 220.01 KiB | 0 | UNSUPPORTED_PIT |
| index_basic | snapshot@20260827 | 20260827 | 1 | 1 | 1 | 8.25 KiB | 0 | COMPLETE |
| suspend_d | 20120101..20251231 | 20120104..20251231 | 1 | 1 | 428,700 | 318.06 KiB | 0 | COMPLETE |
| daily | 20120101..20251231 | 20120104..20251231 | 168 | 168 | 12,498,589 | 406.02 MiB | 0 | COMPLETE |
| daily_basic | 20120101..20251231 | 20120104..20251231 | 168 | 168 | 12,407,491 | 855.72 MiB | 0 | COMPLETE |
| index_daily | 20120101..20251231 | 20120104..20251231 | 168 | 168 | 3,400 | 1.65 MiB | 0 | COMPLETE |

## Daily cross-sectional coverage gate

| Trade date | daily symbols | daily_basic symbols | join symbols | join coverage | missing side | Status |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 20130104 | 2,406 | 2,406 | 2,406 | 1.0000 | daily-only=0; daily_basic-only=0 | PASS |
| 20160104 | 2,592 | 2,549 | 2,549 | 0.9834 | daily-only=43; daily_basic-only=0 | PASS |
| 20180102 | 3,282 | 3,252 | 3,252 | 0.9909 | daily-only=30; daily_basic-only=0 | PASS |
| 20200102 | 3,797 | 3,741 | 3,741 | 0.9853 | daily-only=56; daily_basic-only=0 | PASS |
| 20220104 | 4,737 | 4,670 | 4,670 | 0.9859 | daily-only=67; daily_basic-only=0 | PASS |
| 20240102 | 5,329 | 5,329 | 5,329 | 1.0000 | daily-only=0; daily_basic-only=0 | PASS |
| 20250102 | 5,369 | 5,369 | 5,369 | 1.0000 | daily-only=0; daily_basic-only=0 | PASS |

## Benchmark coverage gate

- Benchmark: `000300.SH`
- Range: `20120104..20251231`
- Sessions: `3400/3400`
- Missing sessions: `0`
- 20D sample: stock=`000001.SZ` date=`20251231`
- 20D stock return: `-0.0121212121212122`
- 20D benchmark return: `0.021825168681704366`
- 20D excess difference: `-0.033946380802916565`
- Status: `PASS`

## Forward evaluation data gate

| Horizon | Eligible as-of sessions | Right-censored tail | Earliest eligible | Latest eligible | Status |
| ---: | ---: | ---: | --- | --- | --- |
| 20D | 3380 | 20 | 20120104 | 20251203 | PASS |
| 60D | 3340 | 60 | 20120104 | 20250930 | PASS |
| 120D | 3280 | 120 | 20120104 | 20250708 | PASS |
| 250D | 3150 | 250 | 20120104 | 20241220 | PASS |

## Dynamic historical symbol sample

| ts_code | list_date | exchange | market | daily range/rows | daily_basic range/rows |
| --- | --- | --- | --- | --- | --- |
| 000001.SZ | 19910403 | SZSE | 主板 | 20120104..20251231 / 3,388 | 20120104..20251231 / 3,388 |
| 301687.SZ | 20251231 | SZSE | 创业板 | 20251231..20251231 / 1 | 20251231..20251231 / 1 |
| 832317.BJ | 20200727 | BSE | 北交所 | 20150914..20211020 / 1,272 | 20181009..20210826 / 4 |
| 600601.SH | 19901219 | SSE | 主板 | 20120104..20251231 / 3,376 | 20120104..20251231 / 3,376 |
| 000004.SZ | 19901201 | SZSE | 主板 | 20120104..20251231 / 3,182 | 20120104..20251231 / 3,182 |
| 000005.SZ | 19901210 | SZSE | 主板 | 20120424..20240305 / 2,782 | 20120424..20240305 / 2,782 |
| 300001.SZ | 20091030 | SZSE | 创业板 | 20120104..20251231 / 3,313 | 20120104..20251231 / 3,313 |
| 833874.BJ | 20200727 | BSE | 北交所 | 20160106..20211105 / 792 | 20200918..20210826 / 2 |
| 688001.SH | 20190722 | SSE | 科创板 | 20190722..20251231 / 1,566 | 20190722..20251231 / 1,566 |
| 000002.SZ | 19910129 | SZSE | 主板 | 20120104..20251231 / 3,249 | 20120104..20251231 / 3,249 |
| 000006.SZ | 19920427 | SZSE | 主板 | 20120104..20251231 / 3,281 | 20120104..20251231 / 3,281 |
| 000007.SZ | 19920413 | SZSE | 主板 | 20120104..20251231 / 2,810 | 20120104..20251231 / 2,810 |
| 000008.SZ | 19920507 | SZSE | 主板 | 20120104..20251231 / 3,131 | 20120104..20251231 / 3,131 |
| 000009.SZ | 19910625 | SZSE | 主板 | 20120104..20251231 / 3,302 | 20120104..20251231 / 3,302 |
| 000010.SZ | 19951027 | SZSE | 主板 | 20120316..20251231 / 3,057 | 20120316..20251231 / 3,057 |
| 000011.SZ | 19920330 | SZSE | 主板 | 20120104..20251231 / 3,399 | 20120104..20251231 / 3,399 |

## RAW integrity

- Unreadable Parquet: `0`
- Zero-byte files: `0`
- tmp/partial files: `0`
- Schema-drift datasets: `NONE`
- Duplicate identities: `NONE`
- Checkpoint/file mismatches: `0`
- Unexpected tiny partitions: `0`
- Integrity status: `PASS`

## Reference PIT findings

| Dataset | Field | Availability | Historical semantics | PIT confidence | Notes |
| --- | --- | --- | --- | --- | --- |
| stock_basic | ts_code/symbol | historical identifier | stable identifier | PIT_SAFE | not a status observation |
| stock_basic | list_date | historical event field | listing boundary | PIT_SAFE | usable only for list_date <= as_of |
| stock_basic | delist_date | historical event field | delisting boundary | PIT_SAFE | usable only when source supplies a non-null date |
| stock_basic | exchange | current snapshot | current reference classification | SNAPSHOT_ONLY | not a dated historical reassignment log |
| stock_basic | market | current snapshot | current board/security category | SNAPSHOT_ONLY | do not project today's board into past dates |
| stock_basic | name | current snapshot | current display name and possible ST label | SNAPSHOT_ONLY | never use as historical ST/name state |
| stock_basic | list_status/is_hs/industry | current snapshot | current status/industry/holding classification | SNAPSHOT_ONLY | not historical PIT state |
| namechange | name/start_date/end_date | historical interval plus ann_date | historical name interval | PIT_WITH_ANN_DATE | only use an interval when ann_date <= as_of |
| namechange | change_reason | historical source row | reason evidence for ST/name changes | PIT_WITH_ANN_DATE | does not by itself prove an eligibility rule |
| suspend_d | trade_date/suspend_type | dated market observation | suspension on the trade date | PIT_BY_TRADE_DATE | no separate publication timestamp is exposed |
| index_basic | all fields | current snapshot | benchmark definition snapshot | SNAPSHOT_ONLY | benchmark identity is explicit; definition history is not claimed |

## Remaining gaps

- stock_basic name/status/industry/board fields are current-snapshot-only; do not project them into historical replay
- namechange is not in the core download: the compatible endpoint probe returned repeated identical rows without a stable exposed source identity; historical ST/name state remains unsupported

## Warnings

- reference_current_snapshot_limits_historical_pit

## Interpretation

`UNSUPPORTED_PIT` for `stock_basic` means the snapshot is complete and useful for static identifiers/listing boundaries, but current name/status/industry/board fields are not projected backward. Historical name intervals require `namechange` plus an announcement-date cutoff; a current snapshot must never be substituted for a historical state.
