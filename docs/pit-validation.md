# PIT prototype check

Synthetic version-chain checks are intentionally separate from live-data checks.

- Live income rows available: `0`

| Scenario | Synthetic result |
| --- | --- |
| 公告前不得可见 | PASS |
| 首次公告后可见首次版本 | PASS |
| 修订前不得可见修订版本 | PASS |
| 修订后可见修订版本 | PASS |

Live checks are reported only when a local sample has been synchronized; no live rows were available in this run if the count above is zero.
