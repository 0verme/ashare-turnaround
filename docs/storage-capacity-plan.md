# Historical RAW storage capacity plan

- Generated at (UTC): `2026-08-27T05:40:07.362511+00:00`
- Data directory: `data`
- Initial free space: `373.87 GiB`
- Initial data directory size: `8.33 MiB`
- Current A-share company count: `5,553` (live stock_basic list_status=L; 2026-08-27)

## Planning assumptions

- Expected revision multiplier: `1.20x`
- Conservative revision multiplier: `1.50x`
- Trading days per year: `245`
- Partition/schema overhead in upper bound: `1.50x`
- Financial/report datasets use four periods per year (Q1, H1, Q3, FY).
- `fina_mainbz` is planned at 32 line items per company-period; forecast at two rows.
- Expected size uses compact re-encoded Zstandard row payload where possible.
  Observed bytes/row from the current tiny sample is also shown and is not used
  blindly because file footer overhead would materially overstate the warehouse.

## Existing sample Parquet measurements

| Dataset | Files | Rows | Size | Rows/file | Bytes/file | Observed bytes/row | Compact bytes/row | Tiny-file signal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| income | 23 | 347 | 1.16 MiB | 15.1 | 51.50 KiB | 3,495.6 | 338.5 | YES |
| balancesheet | 18 | 344 | 1.66 MiB | 19.1 | 94.66 KiB | 5,071.9 | 543.2 | YES |
| cashflow | 22 | 331 | 1.37 MiB | 15.0 | 63.96 KiB | 4,353.3 | 458.3 | YES |
| fina_indicator | 18 | 359 | 1.29 MiB | 19.9 | 73.41 KiB | 3,769.0 | 620.7 | YES |
| fina_mainbz | 1 | 125 | 8.64 KiB | 125.0 | 8.64 KiB | 70.8 | 69.7 | YES |
| forecast | 19 | 70 | 225.58 KiB | 3.7 | 11.87 KiB | 3,300.0 | 405.3 | YES |
| express | 23 | 50 | 263.17 KiB | 2.2 | 11.44 KiB | 5,389.7 | 309.8 | YES |
| fina_audit | 27 | 104 | 165.09 KiB | 3.9 | 6.11 KiB | 1,625.5 | 73.5 | YES |
| disclosure_date | 26 | 296 | 126.56 KiB | 11.4 | 4.87 KiB | 437.8 | 26.6 | YES |
| daily | 100 | 400 | 822.79 KiB | 4.0 | 8.23 KiB | 2,106.4 | 67.2 | YES |
| daily_basic | 100 | 400 | 1.22 MiB | 4.0 | 12.51 KiB | 3,201.9 | 116.6 | YES |

## 10-year order-of-magnitude estimate

| Dataset | Expected rows | Estimated Parquet | Conservative upper bound |
| --- | ---: | ---: | ---: |
| income | 266,544 | 86.05 MiB | 161.34 MiB |
| balancesheet | 266,544 | 138.08 MiB | 258.90 MiB |
| cashflow | 266,544 | 116.49 MiB | 218.41 MiB |
| fina_indicator | 266,544 | 157.79 MiB | 295.85 MiB |
| fina_mainbz | 8,529,408 | 567.06 MiB | 1.04 GiB |
| forecast | 533,088 | 206.05 MiB | 386.35 MiB |
| express | 266,544 | 78.76 MiB | 147.67 MiB |
| fina_audit | 66,636 | 4.67 MiB | 8.75 MiB |
| disclosure_date | 222,120 | 5.64 MiB | 10.57 MiB |
| daily | 13,604,850 | 871.99 MiB | 1.60 GiB |
| daily_basic | 13,604,850 | 1.48 GiB | 2.77 GiB |
| **TOTAL** | **37,893,672** | **3.66 GiB** | **6.86 GiB** |

## 15-year order-of-magnitude estimate

| Dataset | Expected rows | Estimated Parquet | Conservative upper bound |
| --- | ---: | ---: | ---: |
| income | 399,816 | 129.07 MiB | 242.01 MiB |
| balancesheet | 399,816 | 207.12 MiB | 388.35 MiB |
| cashflow | 399,816 | 174.73 MiB | 327.62 MiB |
| fina_indicator | 399,816 | 236.68 MiB | 443.78 MiB |
| fina_mainbz | 12,794,112 | 850.59 MiB | 1.56 GiB |
| forecast | 799,632 | 309.08 MiB | 579.52 MiB |
| express | 399,816 | 118.13 MiB | 221.50 MiB |
| fina_audit | 99,954 | 7.00 MiB | 13.13 MiB |
| disclosure_date | 333,180 | 8.46 MiB | 15.86 MiB |
| daily | 20,407,275 | 1.28 GiB | 2.39 GiB |
| daily_basic | 20,407,275 | 2.22 GiB | 4.16 GiB |
| **TOTAL** | **56,840,508** | **5.49 GiB** | **10.29 GiB** |

## Interpretation

This is a capacity guard, not a forecast of API row availability. Revision rates, line-item counts, schema width, and current listed-company count will be replaced by actual Phase 2A bootstrap measurements. A tiny-file warning means the current sample partition layout must not be extrapolated using raw bytes/file.
