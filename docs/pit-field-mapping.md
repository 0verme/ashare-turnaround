# Financial PIT field mapping

The canonical PIT columns are `report_period`, `announcement_date`,
`actual_available_date`, `report_type`, `update_flag`, `retrieved_at`, and
`source`.  Comparable-period canonicalization additionally adds
`fiscal_year`, `fiscal_period`, `quarter`, `report_family`, `statement_type`,
`duration_semantics`, `scope`, `unit`, `accounting_semantics`,
`source_version_identity`, `source_version`, and
`comparable_period_contract_version`.  See
[comparable-period-semantics.md](comparable-period-semantics.md) for the
versioned economic-period contract.
A row with no usable `actual_available_date` is excluded from an as-of query; the implementation does not invent a date.

| Dataset | report_period | announcement_date | available_date source | Field observation | Semantic status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| income | end_date | ann_date | f_ann_date then ann_date | observed: ann_date, end_date, f_ann_date, report_type, update_flag | confirmed | Live schema and source field definitions confirm f_ann_date as the actual announcement date; ann_date is an explicit fallback. |
| balancesheet | end_date | ann_date | f_ann_date then ann_date | observed: ann_date, end_date, f_ann_date, report_type, update_flag | confirmed | Live schema and source field definitions confirm f_ann_date as the actual announcement date; ann_date is an explicit fallback. |
| cashflow | end_date | ann_date | f_ann_date then ann_date | observed: ann_date, end_date, f_ann_date, report_type, update_flag | confirmed | Live schema and source field definitions confirm f_ann_date as the actual announcement date; ann_date is an explicit fallback. |
| fina_indicator | end_date | ann_date | ann_date | observed: ann_date, end_date, update_flag | confirmed | Live schema and source field definitions confirm ann_date as the endpoint's available announcement date; no f_ann_date is exposed. |
| fina_mainbz | end_date | ann_date | disclosure_date.actual_date (explicit join only) | observed: end_date, update_flag | unknown | Needs an explicit disclosure_date join; actual_date semantics are not assumed. |
| forecast | end_date | ann_date | ann_date | observed: ann_date, end_date, first_ann_date, type, update_flag | confirmed | Live schema and source field definitions confirm ann_date for availability; first_ann_date remains a raw field and is not substituted. |
| express | end_date | ann_date | ann_date | observed: ann_date, end_date, update_flag | confirmed | Live schema and source field definitions confirm ann_date as the available announcement date. |
| fina_audit | end_date | ann_date | ann_date | observed: ann_date, end_date | confirmed | Live schema and source field definitions confirm ann_date as the available announcement date. |
| disclosure_date | end_date | ann_date | actual_date | observed: actual_date, ann_date, end_date | unknown | actual_date is an event field; whether it is data availability is unknown. |

## Evidence status

- `confirmed` is reserved for field presence and semantics established from a live response plus source documentation.
- `suspected` means the implementation has a plausible field mapping but the semantic contract is not fully established.
- `unknown` means the required live evidence was unavailable, or the event field has not been proven to be data availability.
- `disclosure_date.actual_date` is deliberately not treated as availability for `fina_mainbz` without an explicit join. Its semantic meaning remains unknown until verified.

## Version semantics

`report_type` separates report families where present. `update_flag` is retained as a version attribute; PIT selection groups by report identity and picks the latest version whose `actual_available_date` is on or before the `as_of_date`.

## Quarterization scope

Cumulative `income`/`cashflow` values are quarterized only through the
validated Q1, H1-Q1, Q3-H1, and FY-Q3 chain.  Missing, ambiguous, mixed-unit,
mixed-scope, or not-yet-visible links return `UNKNOWN` with a reason.  A
balance sheet is point-in-time and is never quarterized.  Live bounded checks
are recorded in `docs/pit-validation.md`; this is not a factor calculation.
