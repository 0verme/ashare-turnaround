# Tushare-compatible data-source validation

- Generated at (UTC): `2026-08-27T03:29:36Z`
- Sample code: `600000.SH`
- Token configured: `False` (value never recorded)
- Client: official Python `tushare` SDK only; optional Base URL override is confined to `TushareProvider`.
- MCP and seller-specific HTTP APIs are not used by the data chain.

## Ordinary APIs

| API | Status | Rows | Duration (s) | Fields | Notes |
| --- | --- | ---: | ---: | --- | --- |
| stock_basic | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| trade_cal | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| daily | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| daily_basic | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| income | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| balancesheet | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| cashflow | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| fina_indicator | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| fina_mainbz | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| forecast | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| express | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| fina_audit | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |
| disclosure_date | SKIP | 0 | 0.000 | - | TUSHARE_TOKEN is not configured |

## VIP APIs

Not run. Pass `--vip` to validate the optional VIP names separately.

## Interpretation

- `PASS` means the request returned and all minimal required fields were observed.
- `EMPTY` means the request completed but the chosen sample/parameters returned no rows; it is not treated as schema proof.
- `SCHEMA_MISMATCH` means the request completed but required fields were absent.
- `FAIL` includes a classified provider error such as `timeout`, `connection`, `permission`, `not_found`, `rate_limit`, or `compatibility`.
- With no token, all rows are deliberately `SKIP`; this is a credential/configuration block, not evidence that the endpoint is unavailable.

## Pagination and field notes

The validation calls request a small `limit`. Sample synchronization has a bounded limit/offset paginator with a maximum page count; it never retries indefinitely.

## Run status

- Ordinary API availability: `unknown` in this run because no token was configured.
- VIP API availability: `not tested` unless the `--vip` option was used.
- Live pagination behavior, live field presence, and live cumulative-value semantics: `unknown` until an authenticated sample run.
