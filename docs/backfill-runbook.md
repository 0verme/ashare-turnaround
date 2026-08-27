# Unattended resumable backfill runbook

This runbook covers the historical RAW financial backfill orchestrated by the
`bootstrap-financials` command. It is deliberately conservative: it never
deletes existing data, never clears a partition, and never auto-starts a
full-market download. Code engineering and real data movement stay separate.

## Scope and non-goals

- The durable unit of work is one **dataset / report period** (`YYYYMMDD`
  quarter end: `0331`, `0630`, `0930`, `1231`).
- Builds on the single `bootstrap-financials` orchestration path. There is no
  second download path.
- Daily incremental sync is a separate issue and is **not** covered here.
- Scanner factors and derived investment data are out of scope.

## Credentials and configuration

All configuration is environment-backed (`.env` is optional and never
committed).

| Variable | Default | Purpose |
| --- | --- | --- |
| `TUSHARE_TOKEN` | _(none)_ | Required for any real fetch. Without it, bootstrap refuses to run. |
| `TUSHARE_BASE_URL` | official SDK default | Optional compatibility endpoint. Never hard-coded in source. |
| `ASHARE_DATA_DIR` | `./data` | Local RAW Parquet root. Point this at the NAS mount for real runs. |
| `TUSHARE_REQUESTS_PER_MINUTE` | `60` | Global API budget shared by every worker. |
| `TUSHARE_MAX_RETRIES` | `2` | Bounded retry attempts per API call. |
| `TUSHARE_TIMEOUT` | `30` | Per-request timeout (seconds). |

Verify the configuration without a token and without side effects:

```bash
ashare-turnaround preflight
```

Credentials and endpoint URLs are never written to logs, checkpoints, or
reports (`redact_text` strips them).

## Safe defaults

- `--start-year` defaults to `2012`.
- `--end-year` defaults to `latest_complete_annual_year()` (today's year
  minus one). Pass an explicit `--end-year` only when a newer period is known
  to be complete.
- `--workers` defaults to `4`.
- `--requests-per-minute` defaults to `TUSHARE_REQUESTS_PER_MINUTE`.
- `--resume` is on by default. Use `--no-resume` only to force a re-download
  of a period whose durable PASS is known to be wrong.

## Dry-run (no source contact)

Always plan first. The dry-run never constructs a provider, never touches the
network, and creates no local directories:

```bash
ashare-turnaround bootstrap-financials --dry-run --dataset balancesheet \
  --start-year 2024 --end-year 2024
```

It reports the planned dataset/period task count, the worker count, and the
global request budget.

## Restart and resume

Resume is the normal restart path. A rerun skips **only** a period whose
latest checkpoint is a durable `PASS` **and** whose period Parquet file still
exists. Everything else (failed, partial, empty, or missing file) is
re-attempted.

```bash
ashare-turnaround bootstrap-financials --dataset all
```

Interruption (`Ctrl-C`) cancels in-flight fetches and preserves every period
file and checkpoint that was already committed atomically. Re-running the same
command continues from the first unfinished period.

## Failure triage

Checkpoint records live in `data/state/bootstrap-checkpoints.json` (one record
per finished dataset/period unit). `status` meanings:

| Status | Meaning | Resume action |
| --- | --- | --- |
| `PASS` | Full paginated frame written + checkpointed atomically. | Skipped (file still present). |
| `PARTIAL` | Pagination did not reach a provable terminal short page, or a storage/quality warning occurred. Not committed as complete. | Re-attempted. |
| `UNKNOWN_EMPTY` | Zero rows returned; historical availability could not be confirmed. | Re-attempted. |
| `FAILED` | Provider error (timeout/connection/rate-limit/permission) or unexpected exception, isolated to this unit. | Re-attempted. |

A failure in one dataset/period never marks or overwrites an unrelated unit.
Workers only fetch; the coordinator thread is the sole writer of Parquet files
and checkpoint records, so a completed checkpoint always follows an atomic
period-file write.

## Disk guard

`check_disk_space` gates every submission against the data directory.

| Free space | Behavior |
| --- | --- |
| `< 15 GiB` (emergency) | **Hard stop.** Remaining units are marked `FAILED` with `disk emergency stop`; no further fetches, writes, or commits. Never silently proceeds. |
| `< 50 GiB` (stop) | Reported as `STOP`; the emergency gate catches it before any write. |
| `< 100 GiB` (recommended) | Reported as `PROCEED WITH CAUTION`. |
| otherwise | `PASS`. |

A disk-stopped unit has no checkpoint, so resume re-attempts it once space is
reclaimed.

## Rate limiting and safe concurrency ceiling

- One shared `RateLimiter` is attached to the provider before any worker
  starts. Every worker and every retry attempt go through the same limiter, so
  the configured `requests_per_minute` is a true global ceiling regardless of
  `--workers`.
- The default safe ceiling is `--workers 4`. Raising `--workers` does **not**
  increase throughput beyond the rate budget and raises peak memory (each
  in-flight period holds a full period frame before the coordinator writes it).
  Do not raise `--workers` above what the rate limit and available memory
  support; keep the default unless a measured run shows headroom.
- Retry/backoff is bounded and exponential (`TUSHARE_MAX_RETRIES`,
  `backoff_seconds`, jitter), applied inside the worker path so a transient
  failure recovers without aborting the period.

## Verification

After a run, verify durable state from metadata only (no full-frame load):

```bash
ashare-turnaround inventory
```

The manifest reports per-dataset file count, rows, size, earliest/latest
period, and `completeness` (`COMPLETE` only when every checkpointed period is
`PASS`). The checkpoint file and period files are the source of truth for
"complete vs. partial vs. unknown".

## Operational safety rules

- **Never delete `data/`.** Real NAS data is protected.
- **Never clear an existing partition.** Replacement writes are atomic and
  per-period; a rerun does not overwrite a durable PASS.
- **Never auto-start a long full-market backfill** from this runbook. A real
  full-history run is a separate, explicitly approved operation.
- **Never change the real data format** without a compatible reader.
- For real Tushare verification, use only **bounded smoke** runs
  (`sync-sample`, `validate-source`, `validate-vip-production`); do not leave a
  long download running unattended until capacity and the rate budget are
  confirmed.
