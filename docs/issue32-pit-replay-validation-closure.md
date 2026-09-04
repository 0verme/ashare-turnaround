# Issue #32：历史 PIT 代表样本执行与验收闭环

> 本文只记录 correctness validation，不是收益回测。大体量 RAW、checkpoint 和
> gzip artifact 保留在本地 gitignored `data/reports/`；本次 tracked 变更只提交
> 小型审计摘要与 checksum。

## 结论

```text
ISSUE32_READY_TO_CLOSE
```

- 当前基线：`origin/main` / `334ab8c68ab7523d7f5242757727356376a677f4`
- replay 输入 manifest：`de86753eeedb250b8fd8967ae285707e6040602684cae3ce533292c0df69290f`
- replay-relevant corpus：840 files，1,820,573,267 bytes，metadata digest
  `432ab527a4e639df2d359c836b3e7d2ccea913a7a6dbf6432bcc1830f00bfda3`
- full local RAW integrity：`PASS`；4,045 files，139,322,050 rows，
  3,917,634,870 bytes。RAW 未重写、未 compact、未重新下载。

## Existing evidence audit 与复用策略

审计发现十个非 2025-06 frozen members 均已有完整 production single-target
replay、`READY` summary、`COMPLETE` checkpoint、machine audit 和 normalized
artifact。它们均通过 artifact/hash/gzip/JSON syntax/manifest/PIT/resource
审计，因此本次没有启动 aggregate `--stage sample` 重跑，也没有重复成功
snapshot。当前 CLI 没有 artifact-aware resume；在已全部完成的状态下直接调用
aggregate 命令会造成无意义重跑。

`2025-06 / 20250616` 严格复用既有 resource-gate-v3 baseline/repeat：

- `EXISTING_VALIDATED`
- `DO_NOT_RERUN`
- 两个 artifact 均为 2,781,058,369 bytes
- SHA-256：`142082b0649180e09e0dea946feb868f6e831d314c39324c2e69a37a154adce8`
- 5,102 candidates，PIT violations=0，resource gate=`PASS`
- semantic/artifact/determinism pair audit=`PASS`

artifact 路径均为逻辑相对路径，例如
`data/reports/issue32-sample-2019-03/`；实际大文件不进入 Git。

## Layer 1：monthly target schedule

- target months：120
- `AVAILABLE`：108
- `UNAVAILABLE_DATA`：7
- `INCOMPLETE_CURRENT_MONTH`：1
- `UNAVAILABLE_FUTURE`：4
- selection rule：`monthly-anchor-15-v1`
- schedule digest：
  `9869849b0a22a5e64b482677b4cceb1315c027f8222ec842b4af52cd4c310bf8`

没有跨月替代、未来替代或 current universe 替代。

## Layer 2：逐 snapshot 机器审计

| target | selected date | regime | status | candidates | formal Top-N | PIT | resource gate | artifact bytes | SHA-256 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| 2017-01 | 20170116 | range | READY | 2,720 | 3 | 0 | PASS_WITH_WARNING | 846,835,417 | `5c88997c60b97c182b4716a62c864483164f7c78fa91827bb3a172242ca7ae89` |
| 2018-10 | 20181015 | bear | READY | 3,453 | 3 | 0 | PASS_WITH_WARNING | 1,244,078,297 | `3deff7733977fa1ff11a4c0a10704df3924d918ae91702e7e3b6c6df0f270e79` |
| 2019-03 | 20190315 | bull | READY | 3,543 | 3 | 0 | PASS_WITH_WARNING | 1,311,926,398 | `b21cd301b4dd72c0aba1b821eda59c800d2d3fef3097af7b92ad9386845cab15` |
| 2020-01 | 20200115 | range | READY | 3,663 | 3 | 0 | PASS_WITH_WARNING | 1,484,702,397 | `748abb7b827605c6548b74862358adcab60ff5d888b837b686aca8ecaef1479f` |
| 2020-09 | 20200915 | bull | READY | 3,815 | 3 | 0 | PASS_WITH_WARNING | 1,635,362,104 | `ffadb8e94eee8414520c7fe9b69c10fc3ddbd4f97882cc7ded00a0eb309b63ef` |
| 2022-05 | 20220516 | bear | READY | 4,559 | 3 | 0 | PASS_WITH_WARNING | 2,105,463,510 | `7bb8cb7571a42bd9f029d3a4764b19b31fb16fc3b14d165f27c88a9136475e02` |
| 2023-12 | 20231215 | bear | READY | 5,038 | 3 | 0 | PASS_WITH_WARNING | 2,490,753,676 | `62405c52b07f4930d515ccac6bee569818106a444b5f9fba1caea1ca8dd342e9` |
| 2024-05 | 20240515 | range | READY | 5,072 | 3 | 0 | PASS_WITH_WARNING | 2,594,700,178 | `8c142fd1969fe3e0949998c39e7746912f68dcf4402f8f926551e130f8d702d1` |
| 2024-11 | 20241115 | bull | READY | 5,069 | 3 | 0 | PASS_WITH_WARNING | 2,683,178,260 | `e64bedfd8b07259ee1cd9ff5754972a3abc04473921377181ff0a47edc61f518` |
| 2025-12 | 20251215 | range | READY | 5,134 | 3 | 0 | PASS_WITH_WARNING | 2,885,941,834 | `00aa4eaa6407a43c008407988160ac50d4daefd6b37eacb6a9cddbc98a68f4a8` |

每个非复用成员均满足：checkpoint=`COMPLETE`、run=`READY`、missing inputs=0、
PIT violations=0、candidate vector digest count 等于 diagnostic candidate count、
resource telemetry complete、live PSS/private 均低于 6 GiB、
`swap_pressure_active=false`，并保留 formal/diagnostic ranking、universe
decisions、feature vectors、provenance、coverage/confidence/unknowns、score
breakdown、manifest/version/warnings。

所有十个 sample gzip artifact 均通过 `gzip -t`；连同 2025-06 baseline/repeat，
12 个 artifact 均通过完整 streaming JSON syntax 检查。未发现 truncation。

### 资源说明

十个历史成员的 resource gate 均为 `PASS_WITH_WARNING`，仅包含声明的
`system_swap_free_below_soft_floor` 和/或
`system_swap_growth_above_soft_limit`；external monitor 均为 `PASS`，没有
allocator failure、live PSS/private overflow 或 active sustained swap pressure。
这些 warning 没有被降级为 PASS，也没有修改阈值。

## Manual review subset

审查方式为 agent-assisted、无 UI、无 network secret，不检查任何未来收益。
原始 `manual-review.json` 机器预检保留为证据；本次另有小型
`manual-review-signoff.json` 记录最终审查结论。

| 月份 | regime | Top-3 | diagnostic high-score 但 ineligible | unknown-heavy | universe exclusion | PIT boundary | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2019-03 | bull | `600817.SH`, `000543.SZ`, `000055.SZ` | `002940.SZ`，`critical_group_unknown:trend`，未进 formal Top-N | `600421.SH`，fundamental/trend unknown | `000003.SZ`，`delisted_by_as_of` | financial/market/benchmark/current-field checks PASS | PASS |
| 2022-05 | bear | `300343.SZ`, `002759.SZ`, `000792.SZ` | `002255.SZ`，`critical_group_unknown:trend`，未进 formal Top-N | `688192.SH`，fundamental/trend unknown | `000003.SZ`，`delisted_by_as_of` | financial/market/benchmark/current-field checks PASS | PASS |
| 2025-06 | range | `688233.SH`, `002355.SZ`, `688615.SH` | `688286.SH`，`critical_group_unknown:trend`，未进 formal Top-N | `688302.SH`，fundamental/trend unknown | `000003.SZ`，`delisted_by_as_of` | retained v3 pair PIT check PASS | PASS |

审查确认：

- formal candidates 的 universe member、`ranking_eligible=true`、eligibility
  reason、fundamental/trend/quality/attention/expectation-crowding evidence、
  coverage/confidence 和 score breakdown 均可追溯；
- `benchmark_id=000300.SH`，benchmark cutoff 和 `as_of` cutoff 明确；
- financial `actual_available_date` / comparable availability 不越过
  `as_of`；2019-03 与 2022-05 的抽样 normalized vectors 分别有 221 个
  evidence records，所有 refs 完整，且抽样 availability date 没有晚于 as-of；
- unknown groups、missing/invalid fields 保持显式，不做 silent neutral-fill；
- exclusion 只使用 `ts_code/list_date/delist_date`，历史
  name/status/industry/board 仍标记为 `UNSUPPORTED_PIT`。

## Validation gates

- `ruff check .`：PASS
- `python3 -m compileall -q src tests`：PASS
- `PYTHONPATH=src pytest -q`：302 passed，1 skipped（1 个 live integration skip）
- `git diff --check`：PASS（执行审计前）
- synthetic adversarial fixtures：PASS，9/9；包括 revised disclosure、exact
  boundary、future market row、missing benchmark、insufficient history、delisted、
  pre-listing、critical-group gate、deterministic tie
- PIT violations：0
- resource hard failures：0
- allocator failures：0

## Acceptance matrix

| Acceptance criterion | Status | Evidence |
| --- | --- | --- |
| monthly target schedule | PASS | 120 targets；固定 digest 与状态计数 |
| bull/bear/range coverage | PASS | bull 3、bear 3、range 4 个新成员；2025-06 range 复用 |
| complete snapshot evidence | PASS | 10 个 READY full artifacts + 2025-06 v3 pair |
| financial PIT | PASS | 所有 snapshot PIT=0；revision/boundary fixtures PASS |
| market PIT | PASS | 所有 snapshot PIT=0；future-market fixture PASS |
| historical universe | PASS | `historical-universe-v1`；safe fields、完整 decisions、exclusion review |
| evidence-confidence gate | PASS | formal rows 全部 eligible；ineligible/unknown-heavy 仅保留 diagnostic |
| deterministic path | PASS | 2025-06 baseline/repeat 的 semantic、artifact、determinism audit PASS |
| synthetic adversarial fixtures | PASS | 9/9 PASS |
| machine-readable artifacts | PASS | manifest、summary、checkpoint、gzip/hash/JSON audit |
| human-readable summary | PASS | 各 snapshot `summary.md` 与本 closure summary |
| manual review | PASS | 2019-03 bull、2022-05 bear、2025-06 range 完成固定清单 |
| no tuning performed | PASS | 未运行 #17/#18；未做 forward return、调参、Score v2 变更或 RAW rewrite |

## Known limitations

1. 大型 artifact/checkpoint 是本地 gitignored evidence，GitHub PR 只提交小型
   summary/checksum，不提交 RAW、gzip replay、私有路径或 secrets。
2. `stock_basic` 历史 name/status/industry/board 仍是 `UNSUPPORTED_PIT`，没有
   current snapshot fallback。
3. 2020-09 的历史 campaign log 保留了一次外部 harvest 造成的 transient RAW
   metadata drift；最终 replay-relevant postflight 为 unchanged，当前 artifact
   machine audit 为 PASS。本次审计没有删除或重写该证据。
4. artifact 执行源代码记录为 frozen replay semantic commit；PR #42 新增的
   harvest CLI/data archive 不改变 replay-sensitive source files，当前 main 已
   同时包含 PR #41 与 PR #42。

本轮没有启动 Evaluation #17、Ablation/Stability #18，也没有任何收益研究。
