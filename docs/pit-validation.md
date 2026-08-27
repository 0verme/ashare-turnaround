# PIT prototype check

Synthetic version-chain checks are intentionally separate from live-data checks.

- Live income rows available: `347`

| Scenario | Synthetic result |
| --- | --- |
| 公告前不得可见 | PASS |
| 首次公告后可见首次版本 | PASS |
| 修订前不得可见修订版本 | PASS |
| 修订后可见修订版本 | PASS |

## Live PIT evidence

| Check | Status | Evidence |
| --- | --- | --- |
| real basic PIT mapping | PASS | income live schema fields and canonical date mapping |
| real revision candidate | PASS | balancesheet 300001.SZ report_period=2016-12-31; undistr_porfit: 2017-04-17=755215450.39, 2021-12-17=728884443.38 |
| real revision chain | PASS | as-of boundary checks: {'before_first': True, 'after_first': True, 'before_revision': True, 'after_revision': True} |

## Financial period semantics

The audit is limited to at most three local companies and two complete years per dataset. It calculates Q1, H1-Q1, Q3-H1, and FY-Q3; it does not create factors.

| Dataset | Status | Semantic status | Complete company-years | Field bridge checks |
| --- | --- | --- | ---: | --- |
| income | PASS | confirmed | 6 | revenue=6/6, n_income=6/6 |
| cashflow | PASS | suspected | 6 | n_cashflow_act=6/6, net_profit=2/2 |

## Date field interpretation

- `income`, `balancesheet`, and `cashflow`: live `ann_date`, `f_ann_date`, `end_date`, `report_type`, and `update_flag` were observed; `f_ann_date` is preferred for record availability, with `ann_date` as an explicit fallback.
- `disclosure_date.actual_date` was observed as an event date and is not silently substituted for a specific financial record's `f_ann_date`.
- Bounded joins observed income: 312/329 matched; balancesheet: 328/338 matched; cashflow: 309/318 matched; agreement is evidence of correlation, not proof that the event field replaces record availability.

Real revision search is bounded to already synchronized local data; no broad stock scan or historical bootstrap is performed by `pit-check`.
