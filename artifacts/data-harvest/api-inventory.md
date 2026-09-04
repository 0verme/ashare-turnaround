# Tushare API inventory

- Generated at (UTC): `2026-09-01T02:17:40.498192+00:00`
- Data directory: `data`
- Provider: `tushare`
- Endpoint kind: `custom` (private endpoint value is never recorded)
- Token configured: `True`
- Deadline configured: `False`
- Catalog version: `2026-archive-v1`

| API | Dataset | Permission | Category | Priority | Probe | Result | Rows | Earliest | Latest | Partition | Local | PIT |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| report_rc | report_rc | OK | analyst/research | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| cyq_perf | cyq_perf | OK | chip | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| cyq_chips | cyq_chips | OK | chip | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month_trade_date | MISSING | DERIVED_VENDOR_DATA |
| stk_factor | stk_factor | OK | vendor factor | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 1 | 20240131 | 20240131 | year_month_trade_date | MISSING | DERIVED_VENDOR_DATA |
| stk_factor_pro | stk_factor_pro | OK | vendor factor | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 1 | 20240131 | 20240131 | year_month_trade_date | MISSING | DERIVED_VENDOR_DATA |
| adj_factor | adj_factor | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| stock_st | stock_st | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| st | st | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240112 | 20240129 | year | MISSING | UNSUPPORTED_PIT |
| bak_basic | bak_basic | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20260831 | 20260831 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| namechange | namechange | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20060512 | 20061009 | year | MISSING | PARTIAL_OR_UNSUPPORTED |
| stock_company | stock_company | OK | PIT/reference | P0-B | PASS | CURRENT_ONLY | 1 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| new_share | new_share | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240208 | year | MISSING | PIT_REQUIRES_VALIDATION |
| stk_limit | stk_limit | OK | PIT/reference | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| forecast | forecast_archive | OK | financial supplementary | P0-B | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year | MISSING | PIT_REQUIRES_VALIDATION |
| express | express_archive | OK | financial supplementary | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 1 | 20241231 | 20250117 | year | MISSING | PIT_REQUIRES_VALIDATION |
| fina_audit | fina_audit_archive | OK | financial supplementary | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 1 | 20241231 | 20250329 | year | MISSING | PIT_REQUIRES_VALIDATION |
| fina_mainbz | fina_mainbz_archive | OK | financial supplementary | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20241231 | 20241231 | year | MISSING | PIT_REQUIRES_VALIDATION |
| disclosure_date | disclosure_date_archive | OK | financial supplementary | P0-B | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year | MISSING | PIT_REQUIRES_VALIDATION |
| stk_surv | stk_surv | OK | institutional survey | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| broker_recommend | broker_recommend | OK | analyst/research | P0-A | PASS | AVAILABLE_NOT_ARCHIVED | 3 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| share_float | share_float | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240127 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| stk_holdernumber | stk_holdernumber | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240110 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| top10_holders | top10_holders | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240202 | year | MISSING | PIT_REQUIRES_VALIDATION |
| top10_floatholders | top10_floatholders | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20231231 | 20240430 | year | MISSING | PIT_REQUIRES_VALIDATION |
| stk_holdertrade | stk_holdertrade | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| pledge_stat | pledge_stat | OK | ownership/governance | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year | MISSING | PIT_REQUIRES_VALIDATION |
| pledge_detail | pledge_detail | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20230725 | 20240131 | year | MISSING | PIT_REQUIRES_VALIDATION |
| repurchase | repurchase | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20231229 | 20240102 | year | MISSING | PIT_REQUIRES_VALIDATION |
| dividend | dividend | OK | ownership/governance | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20251231 | 20260828 | year | MISSING | PIT_REQUIRES_VALIDATION |
| block_trade | block_trade | OK | event/market behavior | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| moneyflow | moneyflow | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| moneyflow_ths | moneyflow_ths | OK | flow/crowding | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| moneyflow_dc | moneyflow_dc | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| margin | margin | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| margin_detail | margin_detail | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| margin_secs | margin_secs | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| hk_hold | hk_hold | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| ggt_top10 | ggt_top10 | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| ggt_daily | ggt_daily | OK | flow/crowding | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| index_member_all | index_member_all | OK | industry/index | P0-B | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 19970422 | 20020916 | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| index_member | index_member | OK | industry/index | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| index_weight | index_weight | OK | industry/index | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | index_year | MISSING | PIT_REQUIRES_VALIDATION |
| index_daily | index_daily_benchmarks | OK | industry/index | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | index_month | MISSING | PIT_REQUIRES_VALIDATION |
| index_classify | index_classify | OK | industry/index | P1 | PASS | CURRENT_ONLY | 3 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| sw_daily | sw_daily | OK | industry/index | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| sw_member | sw_member | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND | 0 | - | - | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| ci_index | ci_index | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND | 0 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| ci_member | ci_member | UNKNOWN | industry/index | P1 | NOT_FOUND | NOT_FOUND | 0 | - | - | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| ci_daily | ci_daily | OK | industry/index | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| ths_index | ths_index | OK | alternative/attention | P1 | PASS | CURRENT_ONLY | 3 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| ths_member | ths_member | OK | alternative/attention | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| ths_daily | ths_daily | OK | alternative/attention | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| ths_hot | ths_hot | OK | alternative/attention | P1 | PASS | CURRENT_ONLY | 3 | 20260901 | 20260901 | current | MISSING | CURRENT_SNAPSHOT_ONLY |
| ths_hot_rank | ths_hot_rank | UNKNOWN | alternative/attention | P1 | NOT_FOUND | NOT_FOUND | 0 | - | - | current | MISSING | CURRENT_SNAPSHOT_ONLY |
| dc_index | dc_index | OK | alternative/attention | P1 | PASS | CURRENT_ONLY | 3 | 20260831 | 20260831 | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| dc_member | dc_member | OK | alternative/attention | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | index_snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| dc_daily | dc_daily | OK | alternative/attention | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| dc_hot | dc_hot | OK | alternative/attention | P1 | PASS | CURRENT_ONLY | 3 | 20260831 | 20260831 | current | MISSING | CURRENT_SNAPSHOT_ONLY |
| dc_hot_rank | dc_hot_rank | UNKNOWN | alternative/attention | P1 | NOT_FOUND | NOT_FOUND | 0 | - | - | current | MISSING | CURRENT_SNAPSHOT_ONLY |
| top_list | top_list | OK | event/market behavior | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| top_inst | top_inst | OK | event/market behavior | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| limit_list_d | limit_list_d | OK | event/market behavior | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| limit_list_ths | limit_list_ths | OK | event/market behavior | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| limit_list | limit_list | OK | event/market behavior | P2 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| stk_auction | stk_auction | OK | event/market behavior | P1 | EMPTY | AVAILABLE_NOT_ARCHIVED | 0 | - | - | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| stk_auction_c | stk_auction_c | OK | event/market behavior | P1 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| fund_basic | fund_basic | OK | fund/ownership | P2 | PASS | CURRENT_ONLY | 3 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| fund_portfolio | fund_portfolio | OK | fund/ownership | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20241231 | 20250122 | year | MISSING | PIT_REQUIRES_VALIDATION |
| fund_share | fund_share | OK | fund/ownership | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240131 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| fund_manager | fund_manager | OK | fund/ownership | P2 | PASS | CURRENT_ONLY | 2 | 20120328 | 20150612 | snapshot | MISSING | PIT_REQUIRES_VALIDATION |
| fund_company | fund_company | OK | fund/ownership | P2 | PASS | CURRENT_ONLY | 3 | - | - | snapshot | MISSING | CURRENT_SNAPSHOT_ONLY |
| fund_nav | fund_nav | OK | fund/ownership | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240102 | 20240105 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| fund_daily | fund_daily | OK | fund/ownership | P2 | PASS | AVAILABLE_NOT_ARCHIVED | 3 | 20240129 | 20240131 | year_month | MISSING | PIT_REQUIRES_VALIDATION |
| trade_cal | trade_cal | OK | existing market/reference | P0-B | PASS | SKIPPED_EXISTING_COMPLETE | 3 | 20261229 | 20261231 | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| daily | daily | OK | existing market/reference | P0-B | PASS | SKIPPED_EXISTING_COMPLETE | 3 | 20260831 | 20260831 | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| daily_basic | daily_basic | OK | existing market/reference | P0-B | PASS | SKIPPED_EXISTING_COMPLETE | 3 | 20260831 | 20260831 | existing | SKIP_EXISTING_COMPLETE | PIT_REQUIRES_VALIDATION |
| suspend_d | suspend_d | OK | existing market/reference | P0-B | PASS | SKIPPED_EXISTING_COMPLETE | 3 | 19990504 | 19990504 | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| index_basic | index_basic | OK | existing market/reference | P0-B | PASS | SKIPPED_EXISTING_COMPLETE | 3 | - | - | existing | SKIP_EXISTING_COMPLETE | CURRENT_SNAPSHOT_ONLY |
| index_daily | index_daily | UNKNOWN | existing market/reference | P0-B | COMPATIBILITY | SKIPPED_EXISTING_COMPLETE | 0 | - | - | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| income_vip | income | DENIED | existing financial P0 | P0-B | PERMISSION | SKIPPED_EXISTING_COMPLETE | 0 | - | - | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| balancesheet_vip | balancesheet | DENIED | existing financial P0 | P0-B | PERMISSION | SKIPPED_EXISTING_COMPLETE | 0 | - | - | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| cashflow_vip | cashflow | DENIED | existing financial P0 | P0-B | PERMISSION | SKIPPED_EXISTING_COMPLETE | 0 | - | - | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |
| fina_indicator_vip | fina_indicator | DENIED | existing financial P0 | P0-B | PERMISSION | SKIPPED_EXISTING_COMPLETE | 0 | - | - | existing | SKIP_EXISTING_COMPLETE | PIT_SAFE |

## Probe notes

### `report_rc`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Sell-side earnings/target/rating source schema is retained verbatim.

### `cyq_perf`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: High-value chip performance history; no feature integration in this run.

### `cyq_chips`

- Request: `{"ts_code": "600000.SH", "trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Heavyweight price-distribution data; independently gated and resumable.

### `stk_factor`

- Request: `{"ts_code": "600000.SH", "trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Vendor-derived factor history; raw archive only.

### `stk_factor_pro`

- Request: `{"ts_code": "600000.SH", "trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Wide vendor-derived factor history; raw archive only.

### `adj_factor`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Adjustment basis for future return/price research; not used by Scanner here.

### `stock_st`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Historical daily ST state; source publication semantics require validation.

### `st`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: ST event-detail alias; retained separately from stock_st if exposed.

### `bak_basic`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Historical reference snapshot; do not substitute its financial fields for PIT corpus.

### `namechange`

- Request: `{"ts_code": "600000.SH", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Raw history only; download does not resolve historical identity/version semantics.

### `stock_company`

- Request: `{"ts_code": "600000.SH", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Current company reference snapshot, not a historical lifecycle table.

### `new_share`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `stk_limit`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `forecast`

- Request: `{"ts_code": "600000.SH", "period": "20241231", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim
- Error type: `compatibility`
- Notes: Existing raw/forecast is a small sample; archive is isolated and never overwrites it.

### `express`

- Request: `{"ts_code": "600000.SH", "period": "20241231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Error type: `compatibility`
- Notes: Existing raw/express is a small sample; archive is isolated and preserves source schema.

### `fina_audit`

- Request: `{"ts_code": "600000.SH", "period": "20241231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Error type: `compatibility`
- Notes: Existing raw/fina_audit is a small sample; archive is isolated.

### `fina_mainbz`

- Request: `{"ts_code": "600000.SH", "period": "20241231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Error type: `compatibility`
- Notes: Existing raw/fina_mainbz is a small sample; archive is isolated.

### `disclosure_date`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim
- Notes: Event dates remain distinct from financial-record availability dates.

### `stk_surv`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Institutional/research survey source, including raw attention fields.

### `broker_recommend`

- Request: `{"month": "202401", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Broker recommendation/gold-stock candidate API if exposed by the proxy.

### `share_float`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `stk_holdernumber`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `top10_holders`

- Request: `{"ann_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `top10_floatholders`

- Request: `{"ts_code": "600000.SH", "period": "20231231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `stk_holdertrade`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `pledge_stat`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `pledge_detail`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `repurchase`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `dividend`

- Request: `{"ts_code": "600000.SH", "end_date": "20231231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `block_trade`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `moneyflow`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `moneyflow_ths`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim
- Notes: THS provider namespace is kept separate from ordinary moneyflow.

### `moneyflow_dc`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: DC provider namespace is kept separate from ordinary moneyflow.

### `margin`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `margin_detail`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `margin_secs`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `hk_hold`

- Request: `{"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `ggt_top10`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `ggt_daily`

- Request: `{"start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `index_member_all`

- Request: `{"index_code": "801010.SI", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Historical membership in/out dates are retained as source fields.

### `index_member`

- Request: `{"index_code": "000300.SH", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `index_weight`

- Request: `{"index_code": "000300.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `index_daily`

- Request: `{"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Additional benchmarks only; existing primary 000300.SH is not redownloaded.

### `index_classify`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Industry taxonomy snapshot; historical membership is a separate archive.

### `sw_daily`

- Request: `{"ts_code": "801010.SI", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `sw_member`

- Request: `{"index_code": "801010.SI", "limit": 3}`
- Reason: 请指定正确的接口名
- Error type: `not_found`

### `ci_index`

- Request: `{"limit": 3}`
- Reason: 请指定正确的接口名
- Error type: `not_found`

### `ci_member`

- Request: `{"index_code": "CI005001.CI", "limit": 3}`
- Reason: 请指定正确的接口名
- Error type: `not_found`

### `ci_daily`

- Request: `{"ts_code": "CI005001.CI", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `ths_index`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `ths_member`

- Request: `{"ts_code": "885001.TI", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `ths_daily`

- Request: `{"ts_code": "885001.TI", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `ths_hot`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Hot-list endpoint is recorded but not historicalized without stable date coverage.

### `ths_hot_rank`

- Request: `{"limit": 3}`
- Reason: 请指定正确的接口名
- Error type: `not_found`
- Notes: Current snapshot only unless inventory proves historical date support.

### `dc_index`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `dc_member`

- Request: `{"ts_code": "BK00001.DC", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `dc_daily`

- Request: `{"ts_code": "BK00001.DC", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `dc_hot`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `dc_hot_rank`

- Request: `{"limit": 3}`
- Reason: 请指定正确的接口名
- Error type: `not_found`

### `top_list`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `top_inst`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `limit_list_d`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `limit_list_ths`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `limit_list`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `stk_auction`

- Request: `{"ts_code": "600000.SH", "trade_date": "20240131", "limit": 3}`
- Reason: bounded response was empty; no historical completeness claim

### `stk_auction_c`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `fund_basic`

- Request: `{"market": "E", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `fund_portfolio`

- Request: `{"ts_code": "510300.SH", "period": "20241231", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Institutional attention/crowding evidence; not a fund strategy input.

### `fund_share`

- Request: `{"trade_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `fund_manager`

- Request: `{"ts_code": "510300.SH", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `fund_company`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `fund_nav`

- Request: `{"ts_code": "510300.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Large and lower-priority NAV history; no strategy use in this run.

### `fund_daily`

- Request: `{"ts_code": "510300.SH", "start_date": "20240101", "end_date": "20240131", "limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit

### `trade_cal`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Existing 2012-2025 SSE/SZSE calendar is not redownloaded.

### `daily`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Existing 2012-2025 daily corpus is not redownloaded.

### `daily_basic`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Existing 2012-2025 corpus is protected; one prior duplicate checkpoint remains a gap.

### `suspend_d`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Existing 2012-2025 suspension corpus is not redownloaded.

### `index_basic`

- Request: `{"limit": 3}`
- Reason: bounded response returned; historical coverage requires download audit
- Notes: Existing primary benchmark reference snapshot is not redownloaded.

### `index_daily`

- Request: `{"limit": 3}`
- Reason: 必填参数, 标的
- Error type: `compatibility`
- Notes: Existing primary 000300.SH history is not redownloaded; extra benchmarks are separate.

### `income_vip`

- Request: `{"limit": 3}`
- Reason: token不对，您传过来的是<redacted>请确认
- Error type: `permission`
- Notes: Existing validated VIP history is protected.

### `balancesheet_vip`

- Request: `{"limit": 3}`
- Reason: token不对，您传过来的是<redacted>请确认
- Error type: `permission`
- Notes: Existing validated VIP history is protected.

### `cashflow_vip`

- Request: `{"limit": 3}`
- Reason: token不对，您传过来的是<redacted>请确认
- Error type: `permission`
- Notes: Existing validated VIP history is protected.

### `fina_indicator_vip`

- Request: `{"limit": 3}`
- Reason: token不对，您传过来的是<redacted>请确认
- Error type: `permission`
- Notes: Existing validated VIP history is protected.
