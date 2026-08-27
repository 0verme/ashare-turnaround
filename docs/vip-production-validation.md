# VIP production period validation

- Generated at (UTC): `2026-08-27T05:45:43.050280+00:00`
- Period: `20251231`
- Ordinary cross-check sample size: `10` (deterministic random sample)
- VIP calls use the official Tushare Python SDK through `TushareProvider`.
- Credentials and private endpoint configuration are never recorded.
- Page size is recorded in the command/run log; no smoke-test limit was used.

## Full-market results

| Dataset | API | Period | Pages | Rows | Elapsed (s) | Duplicate identities | Schema hash | First ts_code | Last ts_code | PIT fields | Ordinary cross-check | Result |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| income | income_vip | 20251231 | 2 | 7073 | 14.753 | 262 | 8819f99864f1d3a63dac5ff5319585436d811ea850ee2412d8f2c9eb2ad7dc71 | 920269.BJ | 834081.BJ | end_date, ann_date, f_ann_date, report_type, update_flag | PASS | PASS |
| balancesheet | balancesheet_vip | 20251231 | 2 | 6562 | 17.851 | 140 | 4a3866746eeef7c0a2de4b88189a84f1cd2f17274cb8ce09c87ee572ed568ee8 | 688837.SH | 300803.SZ | end_date, ann_date, f_ann_date, report_type, update_flag | PASS | PASS |
| cashflow | cashflow_vip | 20251231 | 2 | 9134 | 27.353 | 143 | da9afed8a137390c7b76e5f88311a2f0d83a039cf42f8077bb376d4f9a93d58f | 920269.BJ | 874538.BJ | end_date, ann_date, f_ann_date, report_type, update_flag | PASS | PASS |
| fina_indicator | fina_indicator_vip | 20251231 | 2 | 6817 | 40.359 | 56 | a73b626d266d3ec4235a31b784e621bda22db6b67acf5bcf925effd9ccb977d2 | 688837.SH | 874400.BJ | end_date, ann_date, update_flag | PASS | PASS |

## Ordinary cross-check details

### income

- Requested codes: `002326.SZ, 002533.SZ, 003003.SZ, 300389.SZ, 300640.SZ, 301057.SZ, 301629.SZ, 600220.SH, 600535.SH, 688699.SH`
- Checked codes: `002326.SZ, 002533.SZ, 003003.SZ, 300389.SZ, 300640.SZ, 301057.SZ, 301629.SZ, 600220.SH, 600535.SH, 688699.SH`
- Status: `PASS`
- Notes: all common raw fields compared

### balancesheet

- Requested codes: `000813.SZ, 002380.SZ, 300373.SZ, 300835.SZ, 301678.SZ, 601966.SH, 605162.SH, 605376.SH, 838897.BJ, 871643.BJ`
- Checked codes: `000813.SZ, 002380.SZ, 300373.SZ, 300835.SZ, 301678.SZ, 601966.SH, 605162.SH, 605376.SH, 838897.BJ, 871643.BJ`
- Status: `PASS`
- Notes: all common raw fields compared

### cashflow

- Requested codes: `300679.SZ, 301201.SZ, 301223.SZ, 301263.SZ, 600262.SH, 600779.SH, 605277.SH, 688216.SH, 688668.SH, 920634.BJ`
- Checked codes: `300679.SZ, 301201.SZ, 301223.SZ, 301263.SZ, 600262.SH, 600779.SH, 605277.SH, 688216.SH, 688668.SH, 920634.BJ`
- Status: `PASS`
- Notes: all common raw fields compared

### fina_indicator

- Requested codes: `002040.SZ, 002852.SZ, 300009.SZ, 300490.SZ, 300723.SZ, 301255.SZ, 600608.SH, 600841.SH, 603676.SH, 873805.BJ`
- Checked codes: `002040.SZ, 002852.SZ, 300009.SZ, 300490.SZ, 300723.SZ, 301255.SZ, 600608.SH, 600841.SH, 603676.SH, 873805.BJ`
- Status: `PASS`
- Notes: all common raw fields compared

## Pagination audit

Each request uses offsets `0, page_size, 2*page_size, ...`; a repeated page
signature, over-limit page, unexpected empty page, or exhausted max-pages bound
is marked PARTIAL rather than treated as HTTP-200 success. The SDK response
surface does not expose a separate API total field, so `total_rows` is recorded
as unavailable unless a future provider adapter exposes it.

### income page log

| Page | Offset | Rows | Elapsed (s) | Schema hash | First | Last |
| ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 | 0 | 5000 | 11.064 | 8819f99864f1d3a63dac5ff5319585436d811ea850ee2412d8f2c9eb2ad7dc71 | 920269.BJ | 600152.SH |
| 2 | 5000 | 2073 | 3.685 | 8819f99864f1d3a63dac5ff5319585436d811ea850ee2412d8f2c9eb2ad7dc71 | 300984.SZ | 834081.BJ |

### balancesheet page log

| Page | Offset | Rows | Elapsed (s) | Schema hash | First | Last |
| ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 | 0 | 5000 | 11.786 | 4a3866746eeef7c0a2de4b88189a84f1cd2f17274cb8ce09c87ee572ed568ee8 | 688837.SH | 920892.BJ |
| 2 | 5000 | 1562 | 6.059 | 4a3866746eeef7c0a2de4b88189a84f1cd2f17274cb8ce09c87ee572ed568ee8 | 874495.BJ | 300803.SZ |

### cashflow page log

| Page | Offset | Rows | Elapsed (s) | Schema hash | First | Last |
| ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 | 0 | 5000 | 11.608 | da9afed8a137390c7b76e5f88311a2f0d83a039cf42f8077bb376d4f9a93d58f | 920269.BJ | 603300.SH |
| 2 | 5000 | 4134 | 15.741 | da9afed8a137390c7b76e5f88311a2f0d83a039cf42f8077bb376d4f9a93d58f | 688330.SH | 874538.BJ |

### fina_indicator page log

| Page | Offset | Rows | Elapsed (s) | Schema hash | First | Last |
| ---: | ---: | ---: | ---: | --- | --- | --- |
| 1 | 0 | 5000 | 30.443 | a73b626d266d3ec4235a31b784e621bda22db6b67acf5bcf925effd9ccb977d2 | 688837.SH | 300234.SZ |
| 2 | 5000 | 1817 | 9.912 | a73b626d266d3ec4235a31b784e621bda22db6b67acf5bcf925effd9ccb977d2 | 920599.BJ | 874400.BJ |

## Gate

- Safe to start P0 historical bootstrap: `YES`
- A failed ordinary cross-check, missing PIT field, EMPTY response, PARTIAL pagination,
  or provider failure blocks VIP bootstrap. Raw rows are never latest-only compacted.
