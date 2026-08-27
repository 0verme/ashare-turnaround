# Market / Reference historical capacity plan

- Generated at (UTC): `2026-08-27T16:29:57.194932+00:00`
- Data directory: `/vol5/1000/ai-workspace/repos/ashare-turnaround/data`
- Research window: `20120101..20251231`
- Benchmark: `000300.SH`
- Exchanges: `SSE, SZSE`
- Estimated trading sessions: `3,430`
- Company count: `5,894` (Stage 1 stock_basic snapshot)
- Sample basis: `bounded/existing sample row width`
- Initial free space: `372.52 GiB`
- Expected total: `1.88 GiB`
- Conservative total: `2.36 GiB`
- Conservative safety margin: `370.16 GiB`
- Capacity gate: **`PASS`**

| Dataset | Estimated rows | Expected size | Conservative | Bytes/row | Basis |
| --- | ---: | ---: | ---: | ---: | --- |
| trade_cal | 10,228 | 639.25 KiB | 958.88 KiB | 64.0 | calendar days x exchanges |
| stock_basic | 5,894 | 1.80 MiB | 2.70 MiB | 320.0 | current L/D/P reference snapshot |
| index_basic | 1 | 220.00 B | 330.00 B | 220.0 | one configured benchmark definition |
| namechange | 47,152 | 8.09 MiB | 12.14 MiB | 180.0 | bounded historical name-change allowance |
| suspend_d | 206,290 | 17.71 MiB | 26.56 MiB | 90.0 | bounded historical suspension allowance |
| daily | 18,441,777 | 618.63 MiB | 773.29 MiB | 35.2 | market-growth adjusted sessions |
| daily_basic | 18,441,777 | 1.25 GiB | 1.57 GiB | 72.9 | market-growth adjusted sessions |
| index_daily | 3,430 | 241.17 KiB | 301.46 KiB | 72.0 | one configured benchmark per session |

The estimate is a preflight guard.  It uses compact Parquet row-width measurements where a bounded/local sample is available, a market-growth adjustment for historical company counts, and conservative partition/schema allowances.  It does not contact the remote provider and it does not rewrite Financial P0 data.
