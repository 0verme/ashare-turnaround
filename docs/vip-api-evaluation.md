# VIP API evaluation

- Generated at (UTC): `2026-08-27T04:30:11Z`
- Token configured: `True` (value never recorded)
- VIP calls are bounded `period=20231231` probes without `ts_code`; a positive result establishes the query mode, not complete pagination or historical coverage.

| Dataset | Ordinary | VIP | Full-market capable | Recommendation |
| --- | --- | --- | --- | --- |
| income | PASS | PASS | YES (bounded period probe) | VIP |
| balancesheet | PASS | PASS | YES (bounded period probe) | VIP |
| cashflow | PASS | PASS | YES (bounded period probe) | VIP |
| fina_indicator | PASS | PASS | YES (bounded period probe) | VIP |
| fina_mainbz | PASS | PASS | YES (bounded period probe) | fallback |
| forecast | PASS | PASS | YES (bounded period probe) | VIP |
| express | PASS | SCHEMA_MISMATCH | NO (required fields missing) | fallback |

## Schema and PIT notes

| Dataset | VIP schema vs ordinary | Missing VIP PIT fields | Notes |
| --- | --- | --- | --- |
| income | same | - | schema=same; pit_mapping=confirmed |
| balancesheet | same | - | schema=same; pit_mapping=confirmed |
| cashflow | same | - | schema=same; pit_mapping=confirmed |
| fina_indicator | same | - | schema=same; pit_mapping=confirmed |
| fina_mainbz | same | - | schema=same; pit_mapping=unknown; requires explicit disclosure_date join |
| forecast | same | - | schema=same; pit_mapping=confirmed |
| express | subset | update_flag | schema=subset; pit_mapping=confirmed; missing_vip_pit_fields=update_flag |

## Recommendation rule

`VIP` requires a successful bounded period probe, an identical or superset schema, no missing raw PIT fields, and a confirmed PIT mapping. `fallback` means ordinary remains usable but VIP is not safe as the primary source. `ordinary` means VIP was not tested or ordinary itself was not ready.
