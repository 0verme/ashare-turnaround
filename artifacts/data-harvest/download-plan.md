# Historical RAW download plan

- Generated at (UTC): `2026-09-01T21:52:25.516833+00:00`
- Range: `20120101..20260831`
- Workers: `4`
- Global rate limit: `60/min`
- Soft guard: `128849018880` bytes; hard guard: `85899345920` bytes
- Checkpoint namespace: `data/state/harvest-checkpoints.json`

| Dataset | API | Priority | Permission | Status | Planned range | Planned units | Existing | Remaining | Estimated requests | Estimated rows | Estimated size | Partition | RAW path | PIT |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| broker_recommend | broker_recommend | P0-A | OK | READY | 20210101..20260831 | 68 | 68 | 0 | 0 | 0 | 0 | year_month | data/raw/broker_recommend | PIT_REQUIRES_VALIDATION |
| cyq_chips | cyq_chips | P0-A | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 3,573 | 0 | 0 | 0 | 0 | 0 | year_month_trade_date | data/raw/cyq_chips | DERIVED_VENDOR_DATA |
| cyq_perf | cyq_perf | P0-A | OK | READY | 20180101..20260831 | 104 | 104 | 0 | 0 | 0 | 0 | year_month | data/raw/cyq_perf | PIT_REQUIRES_VALIDATION |
| report_rc | report_rc | P0-A | OK | READY | 20120101..20260831 | 15 | 15 | 0 | 0 | 0 | 0 | year | data/raw/report_rc | PIT_REQUIRES_VALIDATION |
| stk_factor | stk_factor | P0-A | OK | READY | 20120101..20260831 | 3,573 | 0 | 3,573 | 3,573 | 3,573 | 2000880 | year_month_trade_date | data/raw/stk_factor | DERIVED_VENDOR_DATA |
| stk_factor_pro | stk_factor_pro | P0-A | OK | READY | 20120101..20260831 | 3,573 | 0 | 3,573 | 3,573 | 3,573 | 14920848 | year_month_trade_date | data/raw/stk_factor_pro | DERIVED_VENDOR_DATA |
| stk_surv | stk_surv | P0-A | OK | READY | 20210101..20260831 | 6 | 6 | 0 | 0 | 0 | 0 | year | data/raw/stk_surv | PIT_REQUIRES_VALIDATION |
| adj_factor | adj_factor | P0-B | OK | READY | 20120101..20260831 | 176 | 176 | 0 | 0 | 0 | 0 | year_month | data/raw/adj_factor | PIT_REQUIRES_VALIDATION |
| bak_basic | bak_basic | P0-B | OK | READY | 20160901..20260831 | 120 | 120 | 0 | 0 | 0 | 0 | year_month | data/raw/bak_basic_archive | PIT_REQUIRES_VALIDATION |
| balancesheet | balancesheet_vip | P0-B | DENIED | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/balancesheet | PIT_SAFE |
| cashflow | cashflow_vip | P0-B | DENIED | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/cashflow | PIT_SAFE |
| daily | daily | P0-B | OK | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/daily | PIT_SAFE |
| daily_basic | daily_basic | P0-B | OK | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/daily_basic | PIT_REQUIRES_VALIDATION |
| disclosure_date_archive | disclosure_date | P0-B | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/disclosure_date_archive | PIT_REQUIRES_VALIDATION |
| express_archive | express | P0-B | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/express_archive | PIT_REQUIRES_VALIDATION |
| fina_audit_archive | fina_audit | P0-B | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/fina_audit_archive | PIT_REQUIRES_VALIDATION |
| fina_indicator | fina_indicator_vip | P0-B | DENIED | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/fina_indicator | PIT_SAFE |
| fina_mainbz_archive | fina_mainbz | P0-B | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 58 | 0 | 0 | 0 | 0 | 0 | year | data/raw/fina_mainbz_archive | PIT_REQUIRES_VALIDATION |
| forecast_archive | forecast | P0-B | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/forecast_archive | PIT_REQUIRES_VALIDATION |
| income | income_vip | P0-B | DENIED | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/income | PIT_SAFE |
| index_basic | index_basic | P0-B | OK | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/index_basic | CURRENT_SNAPSHOT_ONLY |
| index_daily | index_daily | P0-B | UNKNOWN | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/index_daily | PIT_SAFE |
| index_member_all | index_member_all | P0-B | OK | READY | 20120101..20260831 | 1 | 1 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/index_member_all | PIT_REQUIRES_VALIDATION |
| namechange | namechange | P0-B | OK | READY | 20120101..20260831 | 15 | 15 | 0 | 0 | 0 | 0 | year | data/raw/namechange | PARTIAL_OR_UNSUPPORTED |
| new_share | new_share | P0-B | OK | READY | 20120101..20260831 | 15 | 14 | 1 | 1 | 3 | 768 | year | data/raw/new_share | PIT_REQUIRES_VALIDATION |
| st | st | P0-B | OK | READY | 20120101..20260831 | 15 | 15 | 0 | 0 | 0 | 0 | year | data/raw/st | UNSUPPORTED_PIT |
| stk_limit | stk_limit | P0-B | OK | READY | 20120101..20260831 | 176 | 176 | 0 | 0 | 0 | 0 | year_month | data/raw/stk_limit | PIT_REQUIRES_VALIDATION |
| stock_company | stock_company | P0-B | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 1 | 0 | 0 | 0 | 0 | snapshot | data/raw/stock_company | CURRENT_SNAPSHOT_ONLY |
| stock_st | stock_st | P0-B | OK | READY | 20151201..20260831 | 129 | 122 | 7 | 7 | 21 | 5376 | year_month | data/raw/stock_st_archive | PIT_REQUIRES_VALIDATION |
| suspend_d | suspend_d | P0-B | OK | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/suspend_d | PIT_SAFE |
| trade_cal | trade_cal | P0-B | OK | SKIP_EXISTING_COMPLETE | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | existing | data/raw/trade_cal | PIT_SAFE |
| block_trade | block_trade | P1 | OK | READY | 20120101..20260831 | 176 | 176 | 0 | 0 | 0 | 0 | year_month | data/raw/block_trade_archive | PIT_REQUIRES_VALIDATION |
| ci_daily | ci_daily | P1 | OK | READY | 20120101..20260831 | 176 | 176 | 0 | 0 | 0 | 0 | year_month | data/raw/ci_daily | PIT_REQUIRES_VALIDATION |
| ci_index | ci_index | P1 | UNKNOWN | CURRENT_ONLY | 20120101..20260831 | 1 | 0 | 1 | 1 | 0 | 0 | snapshot | data/raw/ci_index | CURRENT_SNAPSHOT_ONLY |
| ci_member | ci_member | P1 | UNKNOWN | NOT_FOUND | 20120101..20260831 | 1 | 0 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/ci_member | PIT_REQUIRES_VALIDATION |
| dc_daily | dc_daily | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/dc_daily | PIT_REQUIRES_VALIDATION |
| dc_hot | dc_hot | P1 | OK | CURRENT_ONLY | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | current | data/raw/dc_hot | CURRENT_SNAPSHOT_ONLY |
| dc_hot_rank | dc_hot_rank | P1 | UNKNOWN | CURRENT_ONLY | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | current | data/raw/dc_hot_rank | CURRENT_SNAPSHOT_ONLY |
| dc_index | dc_index | P1 | OK | CURRENT_ONLY | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | current | data/raw/dc_index | CURRENT_SNAPSHOT_ONLY |
| dc_member | dc_member | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 1 | 0 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/dc_member | PIT_REQUIRES_VALIDATION |
| dividend | dividend | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/dividend | PIT_REQUIRES_VALIDATION |
| ggt_daily | ggt_daily | P1 | OK | READY | 20141101..20260831 | 142 | 142 | 0 | 0 | 0 | 0 | year_month | data/raw/ggt_daily | PIT_REQUIRES_VALIDATION |
| ggt_top10 | ggt_top10 | P1 | OK | READY | 20160101..20260831 | 128 | 27 | 101 | 2,020 | 303 | 82416 | year_month | data/raw/ggt_top10 | PIT_REQUIRES_VALIDATION |
| hk_hold | hk_hold | P1 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 176 | 528 | 135168 | year_month | data/raw/hk_hold | PIT_REQUIRES_VALIDATION |
| index_classify | index_classify | P1 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 1 | 0 | 0 | 0 | 0 | snapshot | data/raw/index_classify | CURRENT_SNAPSHOT_ONLY |
| index_daily_benchmarks | index_daily | P1 | OK | READY | 20120101..20260831 | 1,056 | 9 | 1,047 | 1,047 | 3,141 | 804096 | index_month | data/raw/index_daily_benchmarks | PIT_REQUIRES_VALIDATION |
| index_member | index_member | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 1 | 0 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/index_member | PIT_REQUIRES_VALIDATION |
| index_weight | index_weight | P1 | OK | READY | 20120101..20260831 | 15 | 5 | 10 | 10 | 30 | 7680 | index_year | data/raw/index_weight | PIT_REQUIRES_VALIDATION |
| margin | margin | P1 | OK | READY | 20120101..20260831 | 176 | 9 | 167 | 167 | 501 | 128256 | year_month | data/raw/margin | PIT_REQUIRES_VALIDATION |
| margin_detail | margin_detail | P1 | OK | READY | 20120101..20260831 | 176 | 9 | 167 | 167 | 501 | 128256 | year_month | data/raw/margin_detail | PIT_REQUIRES_VALIDATION |
| margin_secs | margin_secs | P1 | OK | READY | 20120101..20260831 | 176 | 9 | 167 | 167 | 501 | 128256 | year_month | data/raw/margin_secs | PIT_REQUIRES_VALIDATION |
| moneyflow | moneyflow | P1 | OK | READY | 20120101..20260831 | 176 | 9 | 167 | 167 | 501 | 160320 | year_month | data/raw/moneyflow | PIT_REQUIRES_VALIDATION |
| moneyflow_dc | moneyflow_dc | P1 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 176 | 528 | 135168 | year_month | data/raw/moneyflow_dc | PIT_REQUIRES_VALIDATION |
| moneyflow_ths | moneyflow_ths | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/moneyflow_ths | PIT_REQUIRES_VALIDATION |
| pledge_detail | pledge_detail | P1 | OK | READY | 20120101..20260831 | 15 | 9 | 6 | 72 | 18 | 4608 | year | data/raw/pledge_detail | PIT_REQUIRES_VALIDATION |
| pledge_stat | pledge_stat | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/pledge_stat | PIT_REQUIRES_VALIDATION |
| repurchase | repurchase | P1 | OK | READY | 20120101..20260831 | 15 | 9 | 6 | 72 | 18 | 4608 | year | data/raw/repurchase | PIT_REQUIRES_VALIDATION |
| share_float | share_float | P1 | OK | READY | 20120101..20260831 | 176 | 109 | 67 | 2,077 | 201 | 51456 | year_month | data/raw/share_float_archive | PIT_REQUIRES_VALIDATION |
| stk_auction | stk_auction | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/stk_auction | PIT_REQUIRES_VALIDATION |
| stk_auction_c | stk_auction_c | P1 | OK | READY | 20120101..20260831 | 176 | 9 | 167 | 2,505 | 501 | 128256 | year_month | data/raw/stk_auction_c | PIT_REQUIRES_VALIDATION |
| stk_holdernumber | stk_holdernumber | P1 | OK | READY | 20120101..20260831 | 15 | 10 | 5 | 60 | 15 | 3840 | year | data/raw/stk_holdernumber | PIT_REQUIRES_VALIDATION |
| stk_holdertrade | stk_holdertrade | P1 | OK | READY | 20120101..20260831 | 15 | 10 | 5 | 60 | 15 | 3840 | year | data/raw/stk_holdertrade | PIT_REQUIRES_VALIDATION |
| sw_daily | sw_daily | P1 | OK | READY | 20120101..20260831 | 176 | 2 | 174 | 174 | 522 | 133632 | year_month | data/raw/sw_daily | PIT_REQUIRES_VALIDATION |
| sw_member | sw_member | P1 | UNKNOWN | NOT_FOUND | 20120101..20260831 | 1 | 0 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/sw_member | PIT_REQUIRES_VALIDATION |
| ths_daily | ths_daily | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/ths_daily | PIT_REQUIRES_VALIDATION |
| ths_hot | ths_hot | P1 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 0 | 1 | 1 | 3 | 768 | snapshot | data/raw/ths_hot | CURRENT_SNAPSHOT_ONLY |
| ths_hot_rank | ths_hot_rank | P1 | UNKNOWN | CURRENT_ONLY | 20120101..20260831 | 0 | 0 | 0 | 0 | 0 | 0 | current | data/raw/ths_hot_rank | CURRENT_SNAPSHOT_ONLY |
| ths_index | ths_index | P1 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 1 | 0 | 0 | 0 | 0 | snapshot | data/raw/ths_index | CURRENT_SNAPSHOT_ONLY |
| ths_member | ths_member | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 1 | 0 | 0 | 0 | 0 | 0 | index_snapshot | data/raw/ths_member | PIT_REQUIRES_VALIDATION |
| top10_floatholders | top10_floatholders | P1 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 15 | 0 | 0 | 0 | 0 | 0 | year | data/raw/top10_floatholders | PIT_REQUIRES_VALIDATION |
| top10_holders | top10_holders | P1 | OK | READY | 20120101..20260831 | 15 | 10 | 5 | 60 | 15 | 3840 | year | data/raw/top10_holders | PIT_REQUIRES_VALIDATION |
| fund_basic | fund_basic | P2 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 0 | 1 | 1 | 3 | 1200 | snapshot | data/raw/fund_basic | CURRENT_SNAPSHOT_ONLY |
| fund_company | fund_company | P2 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 0 | 1 | 1 | 3 | 816 | snapshot | data/raw/fund_company | CURRENT_SNAPSHOT_ONLY |
| fund_daily | fund_daily | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 176 | 528 | 135168 | year_month | data/raw/fund_daily | PIT_REQUIRES_VALIDATION |
| fund_manager | fund_manager | P2 | OK | CURRENT_ONLY | 20120101..20260831 | 1 | 0 | 1 | 1 | 2 | 512 | snapshot | data/raw/fund_manager | PIT_REQUIRES_VALIDATION |
| fund_nav | fund_nav | P2 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/fund_nav | PIT_REQUIRES_VALIDATION |
| fund_portfolio | fund_portfolio | P2 | OK | READY | 20120101..20260831 | 58 | 0 | 58 | 58 | 174 | 44544 | year | data/raw/fund_portfolio | PIT_REQUIRES_VALIDATION |
| fund_share | fund_share | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 2,640 | 528 | 135168 | year_month | data/raw/fund_share | PIT_REQUIRES_VALIDATION |
| limit_list | limit_list | P2 | OK | AVAILABLE_NOT_ARCHIVED | 20120101..20260831 | 176 | 0 | 0 | 0 | 0 | 0 | year_month | data/raw/limit_list | PIT_REQUIRES_VALIDATION |
| limit_list_d | limit_list_d | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 2,640 | 528 | 152064 | year_month | data/raw/limit_list_d | PIT_REQUIRES_VALIDATION |
| limit_list_ths | limit_list_ths | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 2,640 | 528 | 152064 | year_month | data/raw/limit_list_ths | PIT_REQUIRES_VALIDATION |
| top_inst | top_inst | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 2,640 | 528 | 135168 | year_month | data/raw/top_inst | PIT_REQUIRES_VALIDATION |
| top_list | top_list | P2 | OK | READY | 20120101..20260831 | 176 | 0 | 176 | 2,640 | 528 | 135168 | year_month | data/raw/top_list | PIT_REQUIRES_VALIDATION |

## Estimation and safety notes

Estimated rows/bytes are lower-bound planning figures from bounded limit=3 probes; they are not a storage promise. Actual partition results and disk guards control execution.
- `broker_recommend`: Broker recommendation/gold-stock candidate API if exposed by the proxy.; bounded response returned; historical coverage requires download audit
- `cyq_chips`: Heavyweight price-distribution data; independently gated and resumable.; bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `cyq_perf`: High-value chip performance history; no feature integration in this run.; bounded response returned; historical coverage requires download audit
- `report_rc`: Sell-side earnings/target/rating source schema is retained verbatim.; bounded response returned; historical coverage requires download audit
- `stk_factor`: Vendor-derived factor history; raw archive only.; bounded response returned; historical coverage requires download audit
- `stk_factor_pro`: Wide vendor-derived factor history; raw archive only.; bounded response returned; historical coverage requires download audit
- `stk_surv`: Institutional/research survey source, including raw attention fields.; bounded response returned; historical coverage requires download audit
- `adj_factor`: Adjustment basis for future return/price research; not used by Scanner here.; bounded response returned; historical coverage requires download audit
- `bak_basic`: Historical reference snapshot; do not substitute its financial fields for PIT corpus. Earlier month-range responses are retained as invalid-query evidence.; bounded response returned; historical coverage requires download audit
- `balancesheet`: Existing validated VIP history is protected.; token不对，您传过来的是<redacted>请确认
- `cashflow`: Existing validated VIP history is protected.; token不对，您传过来的是<redacted>请确认
- `daily`: Existing 2012-2025 daily corpus is not redownloaded.; bounded response returned; historical coverage requires download audit
- `daily_basic`: Existing 2012-2025 corpus is protected; one prior duplicate checkpoint remains a gap.; bounded response returned; historical coverage requires download audit
- `disclosure_date_archive`: Event dates remain distinct from financial-record availability dates.; bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `express_archive`: Existing raw/express is a small sample; archive is isolated and preserves source schema.; bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `fina_audit_archive`: Existing raw/fina_audit is a small sample; archive is isolated.; bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `fina_indicator`: Existing validated VIP history is protected.; token不对，您传过来的是<redacted>请确认
- `fina_mainbz_archive`: Existing raw/fina_mainbz is a small sample; archive is isolated.; bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `forecast_archive`: Existing raw/forecast is a small sample; archive is isolated and never overwrites it.; bounded response was empty; no historical completeness claim; planned query requires per-security-code retrieval; not archived due volume; bounded probe was empty; no completeness claim
- `income`: Existing validated VIP history is protected.; token不对，您传过来的是<redacted>请确认
- `index_basic`: Existing primary benchmark reference snapshot is not redownloaded.; bounded response returned; historical coverage requires download audit
- `index_daily`: Existing primary 000300.SH history is not redownloaded; extra benchmarks are separate.; 必填参数, 标的
- `index_member_all`: Historical membership in/out dates are retained as source fields.; bounded response returned; historical coverage requires download audit
- `namechange`: Raw history only; download does not resolve historical identity/version semantics.; bounded response returned; historical coverage requires download audit
- `new_share`: bounded response returned; historical coverage requires download audit
- `st`: ST event-detail alias; retained separately from stock_st if exposed.; bounded response returned; historical coverage requires download audit
- `stk_limit`: bounded response returned; historical coverage requires download audit
- `stock_company`: Current company reference snapshot, not a historical lifecycle table.; bounded response returned; historical coverage requires download audit
- `stock_st`: Historical daily ST state; source publication semantics require validation. Prior limit=5000 files remain page-cap-unvalidated evidence.; bounded response returned; historical coverage requires download audit
- `suspend_d`: Existing 2012-2025 suspension corpus is not redownloaded.; bounded response returned; historical coverage requires download audit
- `trade_cal`: Existing 2012-2025 SSE/SZSE calendar is not redownloaded.; bounded response returned; historical coverage requires download audit
- `block_trade`: Prior limit=5000 files remain page-cap-unvalidated evidence.; bounded response returned; historical coverage requires download audit
- `ci_daily`: bounded response returned; historical coverage requires download audit
- `ci_index`: 请指定正确的接口名
- `ci_member`: 请指定正确的接口名
- `dc_daily`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `dc_hot`: bounded response returned; historical coverage requires download audit
- `dc_hot_rank`: 请指定正确的接口名
- `dc_index`: bounded response returned; historical coverage requires download audit
- `dc_member`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `dividend`: bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `ggt_daily`: bounded response returned; historical coverage requires download audit
- `ggt_top10`: bounded response returned; historical coverage requires download audit
- `hk_hold`: bounded response returned; historical coverage requires download audit
- `index_classify`: Industry taxonomy snapshot; historical membership is a separate archive.; bounded response returned; historical coverage requires download audit
- `index_daily_benchmarks`: Additional benchmarks only; existing primary 000300.SH is not redownloaded.; bounded response returned; historical coverage requires download audit
- `index_member`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `index_weight`: bounded response returned; historical coverage requires download audit
- `margin`: bounded response returned; historical coverage requires download audit
- `margin_detail`: bounded response returned; historical coverage requires download audit
- `margin_secs`: bounded response returned; historical coverage requires download audit
- `moneyflow`: bounded response returned; historical coverage requires download audit
- `moneyflow_dc`: DC provider namespace is kept separate from ordinary moneyflow.; bounded response returned; historical coverage requires download audit
- `moneyflow_ths`: THS provider namespace is kept separate from ordinary moneyflow.; bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `pledge_detail`: bounded response returned; historical coverage requires download audit
- `pledge_stat`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `repurchase`: bounded response returned; historical coverage requires download audit
- `share_float`: Earlier range-query files are retained as unvalidated evidence; archive uses ann_date.; bounded response returned; historical coverage requires download audit
- `stk_auction`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `stk_auction_c`: bounded response returned; historical coverage requires download audit
- `stk_holdernumber`: bounded response returned; historical coverage requires download audit
- `stk_holdertrade`: bounded response returned; historical coverage requires download audit
- `sw_daily`: bounded response returned; historical coverage requires download audit
- `sw_member`: 请指定正确的接口名
- `ths_daily`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `ths_hot`: Hot-list endpoint is recorded but not historicalized without stable date coverage.; bounded response returned; historical coverage requires download audit
- `ths_hot_rank`: Current snapshot only unless inventory proves historical date support.; 请指定正确的接口名
- `ths_index`: bounded response returned; historical coverage requires download audit
- `ths_member`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `top10_floatholders`: bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `top10_holders`: bounded response returned; historical coverage requires download audit
- `fund_basic`: bounded response returned; historical coverage requires download audit
- `fund_company`: bounded response returned; historical coverage requires download audit
- `fund_daily`: bounded response returned; historical coverage requires download audit
- `fund_manager`: bounded response returned; historical coverage requires download audit
- `fund_nav`: Large and lower-priority NAV history; no strategy use in this run.; bounded response returned; historical coverage requires download audit; planned query requires per-security-code retrieval; not archived due volume
- `fund_portfolio`: Institutional attention/crowding evidence; not a fund strategy input.; bounded response returned; historical coverage requires download audit
- `fund_share`: bounded response returned; historical coverage requires download audit
- `limit_list`: bounded response was empty; no historical completeness claim; bounded probe was empty; no completeness claim
- `limit_list_d`: bounded response returned; historical coverage requires download audit
- `limit_list_ths`: bounded response returned; historical coverage requires download audit
- `top_inst`: bounded response returned; historical coverage requires download audit
- `top_list`: bounded response returned; historical coverage requires download audit
