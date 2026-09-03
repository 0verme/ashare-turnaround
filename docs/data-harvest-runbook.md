# High-value historical RAW cold archive runbook

This runbook is for the temporary high-permission Tushare window. It is a
**Data Harvest / Cold Archive** workflow, not a strategy or feature change.
The archive deliberately keeps the following states separate:

```text
RAW_ARCHIVED != PIT_VALIDATED != FEATURE_APPROVED
```

No new dataset is connected to Scanner, Score, PIT Replay, Evaluation,
Ablation, or parameter optimization by these commands.

## Safety contract

- Existing validated Financial P0, Market, and Reference partitions are
  protected and are not redownloaded.
- Existing small/incomplete samples are never used as a reason to overwrite
  their original namespace; supplemental archives use an explicit `_archive`
  namespace.
- Workers fetch only. A coordinator writes one deterministic partition with
  an atomic temporary-file/rename/fsync sequence, then appends a checkpoint.
  A successful page or HTTP response is never enough to mark a unit complete.
- Pagination is audited for repeated pages, over-limit pages, unexpected empty
  pages, schema drift, provider failure, and max-page exhaustion.
- One global rate limiter covers all workers and retry attempts.
- No delete, compact, deduplication, or silent latest-row selection is done.

## Commands

Run from the repository root, with `TUSHARE_TOKEN` and the local compatible
endpoint configured only in `.env`/the process environment:

```bash
# 1. Probe only: at most one bounded sample request (+ a safe fallback).
python -m ashare_turnaround harvest-inventory \
  --artifact-dir artifacts/data-harvest

# 2. Dry-run plan: no provider, RAW write, or checkpoint mutation.
python -m ashare_turnaround harvest-plan \
  --inventory artifacts/data-harvest/api-inventory.json \
  --artifact-dir artifacts/data-harvest \
  --start-date 20120101 --end-date 20260831 \
  --workers 4 --requests-per-minute 60

# 3. Resumable harvest. Do not put a token or private URL in arguments.
python -m ashare_turnaround harvest-run \
  --inventory artifacts/data-harvest/api-inventory.json \
  --plan artifacts/data-harvest/download-plan.json \
  --artifact-dir artifacts/data-harvest \
  --workers 4 --page-size 5000 --max-pages 500

# 4. Reconcile after interruption or at the deadline.
python -m ashare_turnaround harvest-audit \
  --inventory artifacts/data-harvest/api-inventory.json \
  --plan artifacts/data-harvest/download-plan.json \
  --artifact-dir artifacts/data-harvest
```

`harvest-run` may return a non-zero code when gaps exist; this is an
operational signal, not permission to delete or rewrite existing data. Rerun
the same plan to resume. Only the latest `PASS` checkpoint for a unit and its
still-readable partition is skipped.

## Deadline modes

Set `ASHARE_HARVEST_DEADLINE` to an actual expiry timestamp (ISO-8601 or Unix
seconds) without committing it:

| Remaining time | Mode | Behavior |
| ---: | --- | --- |
| `>36h` | `OPEN` | New medium/heavy queues may start. |
| `12-36h` | `NO_NEW_HEAVY` | No new heavyweight dataset starts; resume/gap fill continues. |
| `<12h` | `GAP_CLOSING` | No new dataset starts; only already-started units, failures, and reconciliation continue. |
| unset | `NO_DEADLINE` | Disk and queue guards still apply. |

## Disk guards

Defaults are intentionally more conservative than the old Financial bootstrap
emergency threshold:

- `<120 GiB` free: pause `cyq_chips`, `stk_factor`, `stk_factor_pro`, and
  other catalogued heavyweight work; keep high-value small/medium work eligible.
- `<80 GiB` free: stop non-critical remote work immediately. No automatic
  deletion or compaction is attempted.

The guard is checked before queue submission and after every completed unit.

## Coverage and status meanings

`artifacts/data-harvest/coverage.json` records per logical dataset:

```text
COMPLETE
PARTIAL
UNSUPPORTED
CURRENT_ONLY
AVAILABLE_NOT_ARCHIVED
FAILED
SKIPPED_EXISTING_COMPLETE
```

PIT is recorded independently, normally as `PIT_REQUIRES_VALIDATION`,
`PIT_SAFE` only for already validated local corpus metadata,
`CURRENT_SNAPSHOT_ONLY`, `DERIVED_VENDOR_DATA`, or
`PARTIAL_OR_UNSUPPORTED`.

`api-inventory.json` contains the candidate catalog, minimal sample request,
fields, permission classification, estimated partition strategy, and reasons.
It never records a token, private endpoint URL, or secret.

## Integrity checks

`raw-integrity.json` and `.md` check:

- zero-byte and temporary files;
- readable Parquet;
- schema drift by dataset;
- duplicate raw identity rows (reported, never silently removed);
- duplicate checkpoint paths;
- checkpoint/path and checkpoint/row-count mismatches;
- suspicious tiny partitions.

A sparse event dataset may explain tiny partitions in its catalog metadata; an
unexplained tiny cross-sectional partition remains a warning/gap.

## Priority policy

The catalog starts with analyst/research (`report_rc`), chip performance,
historical adjustment/ST/reference, survey, ownership/governance, flow/margin,
industry/index, alternative attention, events, and fund portfolio data. The
following are deliberately last and independently gated:

```text
cyq_chips
stk_factor
stk_factor_pro
```

Current-only hot lists are recorded as `CURRENT_ONLY`, not fabricated into
history. Minute bars, ticks, Level-2, full news/announcement text, and other
very large unstructured sources are not core harvest targets.

## After the window

Do not immediately wire archived fields into Score. First validate each source's
availability date, publication/revision semantics, historical universe,
coverage, duplicate meaning, schema changes, PIT boundary, and usefulness in a
separate review.
