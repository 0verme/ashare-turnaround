# Financial PIT field mapping

The canonical PIT columns are `report_period`, `announcement_date`, `actual_available_date`, `report_type`, `update_flag`, `retrieved_at`, and `source`.
A row with no usable `actual_available_date` is excluded from an as-of query; the implementation does not invent a date.

| Dataset | report_period | announcement_date | available_date source | Field observation | Semantic status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| income | end_date | ann_date | f_ann_date then ann_date | unknown: no live schema run | unknown | Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent. |
| balancesheet | end_date | ann_date | f_ann_date then ann_date | unknown: no live schema run | unknown | Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent. |
| cashflow | end_date | ann_date | f_ann_date then ann_date | unknown: no live schema run | unknown | Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent. |
| fina_indicator | end_date | ann_date | ann_date | unknown: no live schema run | unknown | No f_ann_date candidate is assumed for this endpoint; confirm against live schema. |
| fina_mainbz | end_date | ann_date | disclosure_date.actual_date (explicit join only) | unknown: no live schema run | unknown | Needs an explicit disclosure_date join; actual_date semantics are not assumed. |
| forecast | end_date | ann_date | ann_date | unknown: no live schema run | unknown | first_ann_date is retained as a raw field, not silently substituted. |
| express | end_date | ann_date | ann_date | unknown: no live schema run | unknown | Retain report_type/update_flag and select the latest available version as of the query date. |
| fina_audit | end_date | ann_date | ann_date | unknown: no live schema run | unknown | Retain report_type/update_flag and select the latest available version as of the query date. |
| disclosure_date | end_date | ann_date | actual_date | unknown: no live schema run | unknown | actual_date is an event field; whether it is data availability is unknown. |

## Evidence status

- `confirmed` is reserved for field presence and semantics established from a live response plus source documentation.
- `suspected` means the implementation has a plausible field mapping but this run did not establish its semantic contract.
- `unknown` means the required live evidence was unavailable; it must not be used as a backtest assumption.
- `disclosure_date.actual_date` is deliberately not treated as availability for `fina_mainbz` without an explicit join. Its semantic meaning remains unknown until verified.

## Version semantics

`report_type` separates report families where present. `update_flag` is retained as a version attribute; PIT selection groups by report identity and picks the latest version whose `actual_available_date` is on or before `as_of_date`.

## Quarterization scope

The code contains only a prototype for cumulative `income`/`cashflow` values: Q1, H1-Q1, Q3-H1, and FY-Q3. Whether each live endpoint's values are cumulative is `unknown` until the real API sample and source semantics are verified.
