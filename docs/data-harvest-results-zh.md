# A 股历史 RAW 抢数登记（中文）

> 本登记基于 `artifacts/data-harvest/coverage.json`、`raw-integrity.json` 和 `failures.json`。这里只登记 RAW 归档结果；`RAW_ARCHIVED ≠ PIT_VALIDATED ≠ FEATURE_APPROVED`，未接入 Score/Scanner/Feature/生产。

## 1. 总结

- 计划范围：`data`，`20120101..20260831`。
- 本次新抓到且完整：**18 个逻辑数据集**，54,294,795 行、1,378 文件、628,222,535 bytes。
- 本次新抓到但不完整：**13 个有 RAW 的部分数据集**；另有 `1` 个仅完成尝试但没有有效 RAW 行。
- 原有完整数据：**10 个数据集**，按保护策略跳过重下；共 26,788,227 行、813 文件。
- 物理 RAW 总审计：**4,045 files / 139,322,050 rows / 3,917,634,870 bytes**；包含原有保护数据与本次归档数据。
- 数据集状态计数：`COMPLETE 18`、`PARTIAL 14`、`SKIPPED_EXISTING_COMPLETE 10`、`AVAILABLE_NOT_ARCHIVED 20`、`CURRENT_ONLY 12`、`FAILED 7`、`UNKNOWN 2`。

## 2. 已抢到：完整归档（18 个）

以下分区均 PASS，具备可读 Parquet 和完整分页证明：

| 数据集 | 完成/计划分区 | RAW 行数 | 文件 | 字节 | 请求 | 原始日期范围 | 未完成分区 |
|---|---:|---:|---:|---:|---:|---|---:|
| report_rc | 15/15 | 2,590,395 | 15 | 90,263,930 | 2,434 | 20120101..20260831 | 0 |
| cyq_perf | 104/104 | 9,425,838 | 104 | 123,499,723 | 11,863 | 20180102..20260831 | 0 |
| adj_factor | 176/176 | 13,989,204 | 176 | 29,874,097 | 9,145 | 20120104..20260831 | 0 |
| st | 15/15 | 2,721 | 15 | 414,183 | 15 | 20120118..20260831 | 0 |
| bak_basic | 120/120 | 10,569,309 | 120 | 264,392,435 | 3,249 | 20160901..20260831 | 0 |
| namechange | 15/15 | 7,987 | 15 | 175,200 | 15 | 20120104..20260828 | 0 |
| stk_limit | 176/176 | 15,931,734 | 176 | 83,413,628 | 5,145 | 20120104..20260831 | 0 |
| stk_surv | 6/6 | 2,400 | 6 | 99,719 | 32 | 20211231..20260818 | 0 |
| broker_recommend | 68/68 | 15,884 | 68 | 516,898 | 262 | -..- | 0 |
| stk_holdernumber | 15/15 | 468,321 | 15 | 3,068,857 | 574 | 20020628..20260831 | 0 |
| stk_holdertrade | 15/15 | 179,867 | 15 | 6,004,125 | 267 | 20120104..20260829 | 0 |
| pledge_detail | 15/15 | 291,446 | 15 | 5,921,633 | 390 | 20050828..20560727 | 0 |
| repurchase | 15/15 | 104,249 | 15 | 1,570,987 | 222 | 20111231..20260903 | 0 |
| block_trade | 176/176 | 660,468 | 176 | 12,601,100 | 750 | 20120104..20260831 | 0 |
| ggt_top10 | 128/128 | 46,720 | 128 | 3,669,157 | 2,621 | 20160104..20260831 | 0 |
| ggt_daily | 142/142 | 2,692 | 142 | 878,000 | 142 | 20141117..20260831 | 0 |
| index_member_all | 1/1 | 2,000 | 1 | 43,079 | 1 | 19901219..20260828 | 0 |
| ci_daily | 176/176 | 3,560 | 176 | 1,815,784 | 176 | 20120104..20260831 | 0 |

数据集名单：`report_rc`、`cyq_perf`、`adj_factor`、`st`、`bak_basic`、`namechange`、`stk_limit`、`stk_surv`、`broker_recommend`、`stk_holdernumber`、`stk_holdertrade`、`pledge_detail`、`repurchase`、`block_trade`、`ggt_top10`、`ggt_daily`、`index_member_all`、`ci_daily`。

## 3. 已抢到：部分归档（有 RAW 的 13 个）

这些数据有可用 RAW，但必须按“完成/计划分区”和 gap 使用，不能当作完整历史：

| 数据集 | 完成/计划分区 | RAW 行数 | 文件 | 字节 | 请求 | 原始日期范围 | 未完成分区 |
|---|---:|---:|---:|---:|---:|---|---:|
| stock_st | 122/129 | 342,414 | 122 | 1,485,236 | 409 | 20151205..20260831 | 7 |
| new_share | 14/15 | 3,514 | 14 | 288,609 | 15 | 20120104..20260901 | 1 |
| share_float | 168/176 | 20,429,620 | 168 | 82,716,429 | 27,902 | 20070523..20351029 | 8 |
| top10_holders | 13/15 | 1,159,052 | 13 | 22,492,568 | 1,478 | 20081231..20260831 | 2 |
| moneyflow | 134/176 | 8,600,191 | 134 | 756,885,657 | 10,269 | 20120104..20240229 | 42 |
| margin | 149/176 | 6,342 | 149 | 1,447,617 | 177 | 20120104..20240531 | 27 |
| margin_detail | 149/176 | 4,475,513 | 149 | 187,558,849 | 4,578 | 20120104..20240531 | 27 |
| margin_secs | 149/176 | 5,667,981 | 149 | 9,380,776 | 5,779 | 20120104..20240531 | 27 |
| hk_hold | 86/176 | 4,305,026 | 86 | 36,661,758 | 4,507 | 20160629..20230731 | 90 |
| index_weight | 11/15 | 62,700 | 11 | 272,730 | 73 | 20160129..20260831 | 4 |
| index_daily_benchmarks | 149/1056 | 3,013 | 149 | 1,532,413 | 1,057 | 20120104..20240531 | 907 |
| sw_daily | 135/176 | 2,733 | 135 | 1,708,435 | 176 | 20120801..20231031 | 41 |
| stk_auction_c | 148/176 | 10,774,895 | 148 | 284,454,418 | 12,332 | 20120104..20240430 | 28 |

## 4. 部分尝试但没有抢到有效 RAW

| 数据集 | 完成/计划分区 | RAW 行数 | 文件 | 字节 | 请求 | 原始日期范围 | 未完成分区 |
|---|---:|---:|---:|---:|---:|---|---:|
| moneyflow_dc | 0/176 | 0 | 0 | 0 | 181 | -..- | 176 |

`moneyflow_dc` 的请求返回空覆盖，未写入有效 Parquet；最终保留为 PARTIAL/unknown completeness。

## 5. 已有完整数据：本次明确跳过（10 个）

这些不是本次新下载，但已纳入最终登记，且没有重复下载：

| 数据集 | API | Priority | 结果/说明 |
|---|---|---|---|
| `trade_cal` | `trade_cal` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `daily` | `daily` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `daily_basic` | `daily_basic` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `suspend_d` | `suspend_d` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `index_basic` | `index_basic` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `index_daily` | `index_daily` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `income` | `income_vip` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `balancesheet` | `balancesheet_vip` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `cashflow` | `cashflow_vip` | P0-B | SKIPPED_EXISTING_COMPLETE |
| `fina_indicator` | `fina_indicator_vip` | P0-B | SKIPPED_EXISTING_COMPLETE |

## 6. 没抢到：可用但未归档（20 个）

主要原因是重量级、需要逐证券代码、probe 空覆盖或本轮策略未纳入；没有伪造 COMPLETE：

| 数据集 | API | Priority | 结果/说明 |
|---|---|---|---|
| `cyq_chips` | `cyq_chips` | P0-A | 重量级筹码分布，独立限流/可恢复，最终未归档。 |
| `stk_factor` | `stk_factor` | P0-A | 厂商衍生因子历史，保留为最后 heavyweight 队列，未归档。 |
| `stk_factor_pro` | `stk_factor_pro` | P0-A | 宽表厂商衍生因子历史，保留为最后 heavyweight 队列，未归档。 |
| `forecast_archive` | `forecast` | P0-B | probe 空覆盖；原有小样本隔离保留。 |
| `express_archive` | `express` | P0-B | 原有小样本隔离保留，本轮未归档。 |
| `fina_audit_archive` | `fina_audit` | P0-B | 原有小样本隔离保留，本轮未归档。 |
| `fina_mainbz_archive` | `fina_mainbz` | P0-B | 原有小样本隔离保留，本轮未归档。 |
| `disclosure_date_archive` | `disclosure_date` | P0-B | probe 空覆盖，本轮未归档。 |
| `top10_floatholders` | `top10_floatholders` | P1 | 未进入本轮下载队列。 |
| `pledge_stat` | `pledge_stat` | P1 | probe 空覆盖。 |
| `dividend` | `dividend` | P1 | 未进入本轮下载队列。 |
| `moneyflow_ths` | `moneyflow_ths` | P1 | probe 空覆盖，THS namespace 保持独立。 |
| `index_member` | `index_member` | P1 | probe 空覆盖。 |
| `ths_member` | `ths_member` | P1 | probe 空覆盖。 |
| `ths_daily` | `ths_daily` | P1 | probe 空覆盖。 |
| `dc_member` | `dc_member` | P1 | probe 空覆盖。 |
| `dc_daily` | `dc_daily` | P1 | probe 空覆盖。 |
| `limit_list` | `limit_list` | P2 | probe 空覆盖。 |
| `stk_auction` | `stk_auction` | P1 | probe 空覆盖。 |
| `fund_nav` | `fund_nav` | P2 | 大体量、低优先级，本轮未归档。 |

## 7. 没抢到：运行失败（7 个）

这些数据没有有效 RAW。runner 后段遇到 `token已过期` 后已停止，未继续重试：

| 数据集 | API | Priority | 结果/说明 |
|---|---|---|---|
| `top_list` | `top_list` | P2 | 无 RAW；token 过期时尚未完成。 |
| `top_inst` | `top_inst` | P2 | 无 RAW；token 过期时尚未完成。 |
| `limit_list_d` | `limit_list_d` | P2 | 无 RAW；token 过期时尚未完成。 |
| `limit_list_ths` | `limit_list_ths` | P2 | 无 RAW；token 过期时尚未完成。 |
| `fund_portfolio` | `fund_portfolio` | P2 | 无 RAW；token 过期时尚未完成。 |
| `fund_share` | `fund_share` | P2 | 无 RAW；token 过期时尚未完成。 |
| `fund_daily` | `fund_daily` | P2 | 无 RAW；token 过期时尚未完成。 |

另外，部分已归档数据的缺口来自 provider offset 上限（例如 `share_float`、`moneyflow`），详见 `failures.json` 和 coverage 的 `missing_units`。

## 8. 仅当前快照，不算历史抢到（12 个）

这些接口可探测到当前状态或只适合 snapshot；不把它们包装成历史数据：

`stock_company`、`index_classify`、`ci_index`、`ths_index`、`ths_hot`、`ths_hot_rank`、`dc_index`、`dc_hot`、`dc_hot_rank`、`fund_basic`、`fund_manager`、`fund_company`。

## 9. 未知/未确认（2 个）

`sw_member`、`ci_member`；详情见 `coverage.json`。

## 10. 质量与安全结论

- `raw-integrity.json`：`PASS`。zero-byte、temporary、unreadable Parquet、checkpoint/path mismatch、checkpoint/row-count mismatch 均为 0。
- 审计仍报告既有/原始数据 warning：4 个 schema drift 数据集、558 个文件内重复 identity 行、1,081 个 suspicious-small partitions；均未静默删除或去重。
- 全量测试：`100 passed, 1 skipped`；ruff、compileall 通过。
- token 过期后没有远程重试；无 Score/Scanner/Feature 改动，无生产接入。
- 详细机器可读登记：`coverage.json`；失败明细：`failures.json`；运行方法：`docs/data-harvest-runbook.md`。

## 11. 后续动作

- 若获得新 token，只能从同一 plan/checkpoint resume，先处理上述 gap；不得删除或覆盖已有 raw。
- 先做 availability date、publication/revision semantics、historical universe、duplicate meaning、PIT boundary 验证，再决定是否进入任何生产特征。
