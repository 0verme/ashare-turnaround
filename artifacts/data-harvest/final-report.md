# A-share historical RAW cold archive final report

> This run archives RAW only. `RAW_ARCHIVED` is not `PIT_VALIDATED` and is not `FEATURE_APPROVED`.

## Baseline

- command: `harvest-audit`
- data_dir: `data`
- runner_interrupted: `yes; token expired and process was stopped; atomic RAW/checkpoints preserved`
- remote_retry_after_expiry: `no`
- production_integration: `none`
- disk before: `{'path': 'data', 'free_bytes': 360648740864, 'soft_guard': 128849018880, 'hard_guard': 85899345920, 'action': 'PASS', 'reason': 'free space above guards'}`
- disk after: `{'path': 'data', 'free_bytes': 360648740864, 'soft_guard': 128849018880, 'hard_guard': 85899345920, 'action': 'PASS', 'reason': 'free space above guards'}`

## API inventory

| API | Permission | Category | Priority | Result | Probe |
| --- | --- | --- | --- | --- | --- |
| report_rc | OK | analyst/research | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| cyq_perf | OK | chip | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| cyq_chips | OK | chip | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| stk_factor | OK | vendor factor | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| stk_factor_pro | OK | vendor factor | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| adj_factor | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| stock_st | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| st | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| bak_basic | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| namechange | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| stock_company | OK | PIT/reference | P0-B | CURRENT_ONLY | PASS |
| new_share | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| stk_limit | OK | PIT/reference | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| forecast | OK | financial supplementary | P0-B | AVAILABLE_NOT_ARCHIVED | EMPTY |
| express | OK | financial supplementary | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| fina_audit | OK | financial supplementary | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| fina_mainbz | OK | financial supplementary | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| disclosure_date | OK | financial supplementary | P0-B | AVAILABLE_NOT_ARCHIVED | EMPTY |
| stk_surv | OK | institutional survey | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| broker_recommend | OK | analyst/research | P0-A | AVAILABLE_NOT_ARCHIVED | PASS |
| share_float | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| stk_holdernumber | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| top10_holders | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| top10_floatholders | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| stk_holdertrade | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| pledge_stat | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| pledge_detail | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| repurchase | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| dividend | OK | ownership/governance | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| block_trade | OK | event/market behavior | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| moneyflow | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| moneyflow_ths | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| moneyflow_dc | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| margin | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| margin_detail | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| margin_secs | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| hk_hold | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| ggt_top10 | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| ggt_daily | OK | flow/crowding | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| index_member_all | OK | industry/index | P0-B | AVAILABLE_NOT_ARCHIVED | PASS |
| index_member | OK | industry/index | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| index_weight | OK | industry/index | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| index_daily | OK | industry/index | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| index_classify | OK | industry/index | P1 | CURRENT_ONLY | PASS |
| sw_daily | OK | industry/index | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| sw_member | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND |
| ci_index | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND |
| ci_member | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND |
| ci_daily | OK | industry/index | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| ths_index | OK | alternative/attention | P1 | CURRENT_ONLY | PASS |
| ths_member | OK | alternative/attention | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| ths_daily | OK | alternative/attention | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| ths_hot | OK | alternative/attention | P1 | CURRENT_ONLY | PASS |
| ths_hot_rank | UNKNOWN | alternative/attention | P1 | NOT_FOUND | NOT_FOUND |
| dc_index | OK | alternative/attention | P1 | CURRENT_ONLY | PASS |
| dc_member | OK | alternative/attention | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| dc_daily | OK | alternative/attention | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| dc_hot | OK | alternative/attention | P1 | CURRENT_ONLY | PASS |
| dc_hot_rank | UNKNOWN | alternative/attention | P1 | NOT_FOUND | NOT_FOUND |
| top_list | OK | event/market behavior | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| top_inst | OK | event/market behavior | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| limit_list_d | OK | event/market behavior | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| limit_list_ths | OK | event/market behavior | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| limit_list | OK | event/market behavior | P2 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| stk_auction | OK | event/market behavior | P1 | AVAILABLE_NOT_ARCHIVED | EMPTY |
| stk_auction_c | OK | event/market behavior | P1 | AVAILABLE_NOT_ARCHIVED | PASS |
| fund_basic | OK | fund/ownership | P2 | CURRENT_ONLY | PASS |
| fund_portfolio | OK | fund/ownership | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| fund_share | OK | fund/ownership | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| fund_manager | OK | fund/ownership | P2 | CURRENT_ONLY | PASS |
| fund_company | OK | fund/ownership | P2 | CURRENT_ONLY | PASS |
| fund_nav | OK | fund/ownership | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| fund_daily | OK | fund/ownership | P2 | AVAILABLE_NOT_ARCHIVED | PASS |
| trade_cal | OK | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | PASS |
| daily | OK | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | PASS |
| daily_basic | OK | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | PASS |
| suspend_d | OK | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | PASS |
| index_basic | OK | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | PASS |
| index_daily | UNKNOWN | existing market/reference | P0-B | SKIPPED_EXISTING_COMPLETE | COMPATIBILITY |
| income_vip | DENIED | existing financial P0 | P0-B | SKIPPED_EXISTING_COMPLETE | PERMISSION |
| balancesheet_vip | DENIED | existing financial P0 | P0-B | SKIPPED_EXISTING_COMPLETE | PERMISSION |
| cashflow_vip | DENIED | existing financial P0 | P0-B | SKIPPED_EXISTING_COMPLETE | PERMISSION |
| fina_indicator_vip | DENIED | existing financial P0 | P0-B | SKIPPED_EXISTING_COMPLETE | PERMISSION |

## Archived datasets

| Dataset | Range | Rows | Files | Size | RAW status | PIT status |
| --- | --- | ---: | ---: | ---: | --- | --- |
| report_rc | 20120101..20260831 | 2,590,395 | 15 | 90,263,930 | COMPLETE | PIT_REQUIRES_VALIDATION |
| cyq_perf | 20180102..20260831 | 9,425,838 | 104 | 123,499,723 | COMPLETE | PIT_REQUIRES_VALIDATION |
| cyq_chips | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | DERIVED_VENDOR_DATA |
| stk_factor | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | DERIVED_VENDOR_DATA |
| stk_factor_pro | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | DERIVED_VENDOR_DATA |
| adj_factor | 20120104..20260831 | 13,989,204 | 176 | 29,874,097 | COMPLETE | PIT_REQUIRES_VALIDATION |
| stock_st | 20151205..20260831 | 342,414 | 122 | 1,485,236 | PARTIAL | PIT_REQUIRES_VALIDATION |
| st | 20120118..20260831 | 2,721 | 15 | 414,183 | COMPLETE | UNSUPPORTED_PIT |
| bak_basic | 20160901..20260831 | 10,569,309 | 120 | 264,392,435 | COMPLETE | PIT_REQUIRES_VALIDATION |
| namechange | 20120104..20260828 | 7,987 | 15 | 175,200 | COMPLETE | PARTIAL_OR_UNSUPPORTED |
| new_share | 20120104..20260901 | 3,514 | 14 | 288,609 | PARTIAL | PIT_REQUIRES_VALIDATION |
| stk_limit | 20120104..20260831 | 15,931,734 | 176 | 83,413,628 | COMPLETE | PIT_REQUIRES_VALIDATION |
| forecast_archive | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| express_archive | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| fina_audit_archive | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| fina_mainbz_archive | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| disclosure_date_archive | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| stk_surv | 20211231..20260818 | 2,400 | 6 | 99,719 | COMPLETE | PIT_REQUIRES_VALIDATION |
| broker_recommend | -..- | 15,884 | 68 | 516,898 | COMPLETE | PIT_REQUIRES_VALIDATION |
| share_float | 20070523..20351029 | 20,429,620 | 168 | 82,716,429 | PARTIAL | PIT_REQUIRES_VALIDATION |
| stk_holdernumber | 20020628..20260831 | 468,321 | 15 | 3,068,857 | COMPLETE | PIT_REQUIRES_VALIDATION |
| top10_holders | 20081231..20260831 | 1,159,052 | 13 | 22,492,568 | PARTIAL | PIT_REQUIRES_VALIDATION |
| top10_floatholders | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| stk_holdertrade | 20120104..20260829 | 179,867 | 15 | 6,004,125 | COMPLETE | PIT_REQUIRES_VALIDATION |
| pledge_stat | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| pledge_detail | 20050828..20560727 | 291,446 | 15 | 5,921,633 | COMPLETE | PIT_REQUIRES_VALIDATION |
| repurchase | 20111231..20260903 | 104,249 | 15 | 1,570,987 | COMPLETE | PIT_REQUIRES_VALIDATION |
| dividend | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| block_trade | 20120104..20260831 | 660,468 | 176 | 12,601,100 | COMPLETE | PIT_REQUIRES_VALIDATION |
| moneyflow | 20120104..20240229 | 8,600,191 | 134 | 756,885,657 | PARTIAL | PIT_REQUIRES_VALIDATION |
| moneyflow_ths | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| moneyflow_dc | -..- | 0 | 0 | 0 | PARTIAL | PIT_REQUIRES_VALIDATION |
| margin | 20120104..20240531 | 6,342 | 149 | 1,447,617 | PARTIAL | PIT_REQUIRES_VALIDATION |
| margin_detail | 20120104..20240531 | 4,475,513 | 149 | 187,558,849 | PARTIAL | PIT_REQUIRES_VALIDATION |
| margin_secs | 20120104..20240531 | 5,667,981 | 149 | 9,380,776 | PARTIAL | PIT_REQUIRES_VALIDATION |
| hk_hold | 20160629..20230731 | 4,305,026 | 86 | 36,661,758 | PARTIAL | PIT_REQUIRES_VALIDATION |
| ggt_top10 | 20160104..20260831 | 46,720 | 128 | 3,669,157 | COMPLETE | PIT_REQUIRES_VALIDATION |
| ggt_daily | 20141117..20260831 | 2,692 | 142 | 878,000 | COMPLETE | PIT_REQUIRES_VALIDATION |
| index_member_all | 19901219..20260828 | 2,000 | 1 | 43,079 | COMPLETE | PIT_REQUIRES_VALIDATION |
| index_member | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| index_weight | 20160129..20260831 | 62,700 | 11 | 272,730 | PARTIAL | PIT_REQUIRES_VALIDATION |
| index_daily_benchmarks | 20120104..20240531 | 3,013 | 149 | 1,532,413 | PARTIAL | PIT_REQUIRES_VALIDATION |
| sw_daily | 20120801..20231031 | 2,733 | 135 | 1,708,435 | PARTIAL | PIT_REQUIRES_VALIDATION |
| ci_daily | 20120104..20260831 | 3,560 | 176 | 1,815,784 | COMPLETE | PIT_REQUIRES_VALIDATION |
| ths_member | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| ths_daily | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| dc_member | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| dc_daily | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| top_list | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| top_inst | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| limit_list_d | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| limit_list_ths | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| limit_list | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| stk_auction | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| stk_auction_c | 20120104..20240430 | 10,774,895 | 148 | 284,454,418 | PARTIAL | PIT_REQUIRES_VALIDATION |
| fund_portfolio | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| fund_share | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |
| fund_nav | -..- | 0 | 0 | 0 | AVAILABLE_NOT_ARCHIVED | PIT_REQUIRES_VALIDATION |
| fund_daily | -..- | 0 | 0 | 0 | FAILED | PIT_REQUIRES_VALIDATION |

## Existing data skipped

- `trade_cal`: `SKIP_EXISTING_COMPLETE` (data/raw/trade_cal)
- `daily`: `SKIP_EXISTING_COMPLETE` (data/raw/daily)
- `daily_basic`: `SKIP_EXISTING_COMPLETE` (data/raw/daily_basic)
- `suspend_d`: `SKIP_EXISTING_COMPLETE` (data/raw/suspend_d)
- `index_basic`: `SKIP_EXISTING_COMPLETE` (data/raw/index_basic)
- `index_daily`: `SKIP_EXISTING_COMPLETE` (data/raw/index_daily)
- `income`: `SKIP_EXISTING_COMPLETE` (data/raw/income)
- `balancesheet`: `SKIP_EXISTING_COMPLETE` (data/raw/balancesheet)
- `cashflow`: `SKIP_EXISTING_COMPLETE` (data/raw/cashflow)
- `fina_indicator`: `SKIP_EXISTING_COMPLETE` (data/raw/fina_indicator)

## Heavy dataset status

- `cyq_chips`: `AVAILABLE_NOT_ARCHIVED`; missing units=3573
- `stk_factor`: `AVAILABLE_NOT_ARCHIVED`; missing units=3573
- `stk_factor_pro`: `AVAILABLE_NOT_ARCHIVED`; missing units=3573

## Gap report

- `cyq_chips`: `AVAILABLE_NOT_ARCHIVED`; missing=3573; limitations=Heavyweight price-distribution data; independently gated and resumable.
- `stk_factor`: `AVAILABLE_NOT_ARCHIVED`; missing=3573; limitations=Vendor-derived factor history; raw archive only.
- `stk_factor_pro`: `AVAILABLE_NOT_ARCHIVED`; missing=3573; limitations=Wide vendor-derived factor history; raw archive only.
- `stock_st`: `PARTIAL`; missing=7; limitations=Historical daily ST state; source publication semantics require validation. Prior limit=5000 files remain page-cap-unvalidated evidence.
- `stock_company`: `CURRENT_ONLY`; missing=0; limitations=Current company reference snapshot, not a historical lifecycle table.
- `new_share`: `PARTIAL`; missing=1; limitations=-
- `forecast_archive`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=probe_empty; Existing raw/forecast is a small sample; archive is isolated and never overwrites it.
- `express_archive`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=Existing raw/express is a small sample; archive is isolated and preserves source schema.
- `fina_audit_archive`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=Existing raw/fina_audit is a small sample; archive is isolated.
- `fina_mainbz_archive`: `AVAILABLE_NOT_ARCHIVED`; missing=58; limitations=Existing raw/fina_mainbz is a small sample; archive is isolated.
- `disclosure_date_archive`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=probe_empty; Event dates remain distinct from financial-record availability dates.
- `share_float`: `PARTIAL`; missing=8; limitations=Earlier range-query files are retained as unvalidated evidence; archive uses ann_date.
- `top10_holders`: `PARTIAL`; missing=2; limitations=-
- `top10_floatholders`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=-
- `pledge_stat`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=probe_empty
- `dividend`: `AVAILABLE_NOT_ARCHIVED`; missing=15; limitations=-
- `moneyflow`: `PARTIAL`; missing=42; limitations=-
- `moneyflow_ths`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=probe_empty; THS provider namespace is kept separate from ordinary moneyflow.
- `moneyflow_dc`: `PARTIAL`; missing=176; limitations=DC provider namespace is kept separate from ordinary moneyflow.
- `margin`: `PARTIAL`; missing=27; limitations=-
- `margin_detail`: `PARTIAL`; missing=27; limitations=-
- `margin_secs`: `PARTIAL`; missing=27; limitations=-
- `hk_hold`: `PARTIAL`; missing=90; limitations=-
- `index_member`: `AVAILABLE_NOT_ARCHIVED`; missing=1; limitations=probe_empty
- `index_weight`: `PARTIAL`; missing=4; limitations=-
- `index_daily_benchmarks`: `PARTIAL`; missing=907; limitations=Additional benchmarks only; existing primary 000300.SH is not redownloaded.
- `index_classify`: `CURRENT_ONLY`; missing=0; limitations=Industry taxonomy snapshot; historical membership is a separate archive.
- `sw_daily`: `PARTIAL`; missing=41; limitations=-
- `sw_member`: `UNKNOWN`; missing=1; limitations=-
- `ci_index`: `CURRENT_ONLY`; missing=1; limitations=-
- `ci_member`: `UNKNOWN`; missing=1; limitations=-
- `ths_index`: `CURRENT_ONLY`; missing=0; limitations=-
- `ths_member`: `AVAILABLE_NOT_ARCHIVED`; missing=1; limitations=probe_empty
- `ths_daily`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=probe_empty
- `ths_hot`: `CURRENT_ONLY`; missing=1; limitations=Hot-list endpoint is recorded but not historicalized without stable date coverage.
- `ths_hot_rank`: `CURRENT_ONLY`; missing=0; limitations=Current snapshot only unless inventory proves historical date support.
- `dc_index`: `CURRENT_ONLY`; missing=0; limitations=-
- `dc_member`: `AVAILABLE_NOT_ARCHIVED`; missing=1; limitations=probe_empty
- `dc_daily`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=probe_empty
- `dc_hot`: `CURRENT_ONLY`; missing=0; limitations=-
- `dc_hot_rank`: `CURRENT_ONLY`; missing=0; limitations=-
- `top_list`: `FAILED`; missing=176; limitations=-
- `top_inst`: `FAILED`; missing=176; limitations=-
- `limit_list_d`: `FAILED`; missing=176; limitations=-
- `limit_list_ths`: `FAILED`; missing=176; limitations=-
- `limit_list`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=probe_empty
- `stk_auction`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=probe_empty
- `stk_auction_c`: `PARTIAL`; missing=28; limitations=-
- `fund_basic`: `CURRENT_ONLY`; missing=1; limitations=-
- `fund_portfolio`: `FAILED`; missing=58; limitations=Institutional attention/crowding evidence; not a fund strategy input.
- `fund_share`: `FAILED`; missing=176; limitations=-
- `fund_manager`: `CURRENT_ONLY`; missing=1; limitations=-
- `fund_company`: `CURRENT_ONLY`; missing=1; limitations=-
- `fund_nav`: `AVAILABLE_NOT_ARCHIVED`; missing=176; limitations=Large and lower-priority NAV history; no strategy use in this run.
- `fund_daily`: `FAILED`; missing=176; limitations=-

## RAW integrity

- status: `PASS`
- zero-byte files: `0`
- temporary files: `0`
- unreadable Parquet: `0`
- checkpoint/path mismatches: `0`
- checkpoint/row-count mismatches: `0`
- suspicious small partitions: `1081`

## Capacity

- disk before: `{'path': 'data', 'free_bytes': 360648740864, 'soft_guard': 128849018880, 'hard_guard': 85899345920, 'action': 'PASS', 'reason': 'free space above guards'}`
- disk after: `{'path': 'data', 'free_bytes': 360648740864, 'soft_guard': 128849018880, 'hard_guard': 85899345920, 'action': 'PASS', 'reason': 'free space above guards'}`
- new result bytes recorded by runner: `0`

## Tests

- pytest: `227 passed, 1 skipped (live integration intentionally skipped)`
- ruff: `pass`
- compileall: `pass`

## Scheduler summary

- workers: `4`
- rate limit: `60/min`
- range: `20120101..20260831`

## Next step

> 本轮只完成 RAW 历史数据归档。下一步不要马上接入 Score；应逐数据集进行 PIT semantics、coverage、feature usefulness validation。
