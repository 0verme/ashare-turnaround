from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from ashare_turnaround.scanner.artifacts import (
    canonical_json_bytes,
    expand_normalized_snapshot,
)
from ashare_turnaround.scanner.replay import ReplayConfig, ReplayDiagnostics, ReplayResult
from ashare_turnaround.scanner.replay_validation import (
    FROZEN_REPRESENTATIVE_SAMPLE,
    HISTORICAL_UNIVERSE_CONTRACT_VERSION,
    MANUAL_REVIEW_SAMPLE_MONTHS,
    MARKET_REGIME_CONTRACT_VERSION,
    MONTHLY_SELECTION_RULE_VERSION,
    MONTHLY_TARGET_SCHEDULE_CONTRACT_VERSION,
    PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
    REPRESENTATIVE_SAMPLE_CONTRACT_VERSION,
    REPRESENTATIVE_SAMPLE_RULE_VERSION,
    RESOURCE_GATE_CONTRACT_VERSION,
    RESOURCE_PRESSURE_CONTRACT_VERSION,
    RESOURCE_SAMPLING_CONTRACT_VERSION,
    RESOURCE_WARNING_PROCESS_SWAP,
    RESOURCE_WARNING_SWAP_FREE,
    RESOURCE_WARNING_SWAP_GROWTH,
    MonthlySnapshotTarget,
    ResourceBlocked,
    build_input_manifest,
    classify_market_regime,
    monthly_target_schedule_digest,
    representative_sample_contract,
    run_adversarial_fixtures,
    run_replay_validation,
    run_replay_validation_frames,
    select_monthly_snapshot_dates,
    select_representative_snapshot_targets,
    validate_normalized_snapshot_pit,
    validate_replay_pit,
    write_replay_validation_artifacts,
)
from ashare_turnaround.scanner.universe import UniverseConfig, build_investable_universe
from ashare_turnaround.storage.parquet import RawParquetStore

CODE = "600000.SH"
BENCHMARK = "000300.SH"


def _calendar(start: str = "2025-03-03", end: str = "2025-06-30") -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="B")
    return pd.DataFrame(
        {
            "exchange": ["SSE"] * len(dates),
            "cal_date": dates.strftime("%Y%m%d"),
            "is_open": [1] * len(dates),
        }
    )


def _validation_frames() -> dict[str, pd.DataFrame]:
    calendar = _calendar()
    dates = calendar["cal_date"].tolist()
    stock_dates = dates
    market = pd.DataFrame(
        {
            "ts_code": [CODE] * len(stock_dates),
            "trade_date": stock_dates,
            "close": [10.0 + index * 0.03 for index in range(len(stock_dates))],
            "vol": [1000.0 + index * 2 for index in range(len(stock_dates))],
        }
    )
    daily_basic = pd.DataFrame(
        {
            "ts_code": [CODE] * len(stock_dates),
            "trade_date": stock_dates,
            "close": market["close"],
            "turnover_rate": [1.0 + (index % 5) * 0.1 for index in range(len(stock_dates))],
            "amount": [10000.0 + index * 10 for index in range(len(stock_dates))],
        }
    )
    periods = ["20240331", "20240630", "20240930", "20241231"]
    available = ["20240430", "20240830", "20241030", "20250330"]
    common = {
        "ts_code": [CODE] * 4,
        "end_date": periods,
        "ann_date": available,
        "f_ann_date": available,
        "report_type": ["1"] * 4,
        "update_flag": ["0"] * 4,
    }
    income = pd.DataFrame(
        {
            **common,
            "revenue": [100.0, 115.0, 135.0, 160.0],
            "n_income_attr_p": [4.0, 7.0, 11.0, 17.0],
            "operate_profit": [8.0, 11.0, 16.0, 23.0],
            "gross_profit": [25.0, 30.0, 38.0, 48.0],
            "total_profit": [5.0, 8.0, 12.0, 18.0],
            "non_oper_income": [0.2, 0.2, 0.3, 0.3],
            "assets_impair_loss": [0.1, 0.1, 0.1, 0.1],
        }
    )
    balance = pd.DataFrame(
        {
            **common,
            "total_assets": [200.0, 205.0, 210.0, 220.0],
            "total_hldr_eqy_inc_min_int": [100.0, 102.0, 105.0, 110.0],
            "total_liab": [100.0, 103.0, 105.0, 110.0],
            "inventories": [20.0, 21.0, 22.0, 23.0],
            "accounts_receiv": [18.0, 19.0, 20.0, 21.0],
        }
    )
    cashflow = pd.DataFrame({**common, "n_cashflow_act": [6.0, 8.0, 12.0, 18.0]})
    return {
        "stock_basic": pd.DataFrame(
            {
                "ts_code": [CODE],
                "symbol": ["600000"],
                "name": ["Current name must not be projected"],
                "list_status": ["L"],
                "list_date": ["20100101"],
            }
        ),
        "trade_cal": calendar,
        "daily": market,
        "daily_basic": daily_basic,
        "index_basic": pd.DataFrame({"ts_code": [BENCHMARK], "name": ["CSI 300"]}),
        "index_daily": pd.DataFrame(
            {
                "ts_code": [BENCHMARK] * len(stock_dates),
                "trade_date": stock_dates,
                "close": [100.0 + index * 0.02 for index in range(len(stock_dates))],
            }
        ),
        "suspend_d": pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]),
        "disclosure_date": pd.DataFrame(),
        "income": income,
        "balancesheet": balance,
        "cashflow": cashflow,
        # The production feature path does not consume this group yet, but the
        # validation corpus still treats its absence as an explicit input gap.
        "fina_indicator": pd.DataFrame(
            {"ts_code": [CODE], "end_date": ["20241231"], "ann_date": ["20250330"]}
        ),
    }


def test_monthly_selection_uses_fixed_anchor_and_does_not_cross_month() -> None:
    calendar = pd.DataFrame(
        {
            "exchange": ["SSE"] * 5,
            "cal_date": ["20250114", "20250115", "20250116", "20250214", "20250217"],
            "is_open": [1, 0, 1, 0, 1],
        }
    )

    targets = select_monthly_snapshot_dates(
        calendar,
        "2025-01",
        "2025-03",
        today="2025-12-31",
    )

    assert targets[0].selected_trading_date == "20250116"
    assert targets[0].selection_reason.startswith("first open trade_cal")
    assert targets[1].selected_trading_date == "20250217"
    assert targets[2].status == "UNAVAILABLE"
    assert all(target.calendar_source == "trade_cal" for target in targets)
    assert all(target.status != "AVAILABLE" or target.selected_trading_date for target in targets)


def test_future_months_are_marked_unavailable_without_neighbor_substitution() -> None:
    calendar = _calendar("2025-01-02", "2025-03-31")
    targets = select_monthly_snapshot_dates(
        calendar,
        "2025-02",
        "2026-02",
        today="2025-03-31",
    )

    future = [target for target in targets if target.target_month == "2026-02"][0]
    assert future.status == "UNAVAILABLE_FUTURE"
    assert future.selected_trading_date is None
    assert future.selection_reason.endswith("future substitution")


def test_frozen_validation_cutoff_separates_historical_current_and_future_targets() -> None:
    calendar = pd.DataFrame(
        {
            "exchange": ["SSE"] * 4,
            "cal_date": ["20250616", "20260817", "20260828", "20260901"],
            "is_open": [1] * 4,
        }
    )
    targets = select_monthly_snapshot_dates(
        calendar,
        "2025-06",
        "2026-09",
        today="20260830",
    )
    by_month = {target.target_month: target for target in targets}

    historical = by_month["2025-06"]
    assert historical.selected_trading_date == "20250616"
    assert historical.incomplete_month is False
    current = by_month["2026-08"]
    assert current.status == "AVAILABLE"
    assert current.selected_trading_date == "20260817"
    assert current.incomplete_month is True
    future = by_month["2026-09"]
    assert future.status == "UNAVAILABLE_FUTURE"
    assert future.selected_trading_date is None


def test_target_schedule_has_explicit_data_and_incomplete_statuses() -> None:
    calendar = pd.DataFrame(
        {
            "exchange": ["SSE"] * 2,
            "cal_date": ["20260116", "20260216"],
            "is_open": [1, 1],
        }
    )
    targets = select_monthly_snapshot_dates(
        calendar,
        "2026-01",
        "2026-03",
        today="2026-02-20",
    )
    assert targets[0].availability_status == "AVAILABLE"
    assert targets[0].selection_rule_version == MONTHLY_SELECTION_RULE_VERSION
    assert targets[1].availability_status == "INCOMPLETE_CURRENT_MONTH"
    assert targets[1].incomplete_month is True
    assert targets[2].availability_status == "UNAVAILABLE_FUTURE"
    assert targets[2].unavailable_reason


def test_frozen_representative_sample_is_exact_and_never_falls_back() -> None:
    targets = tuple(
        MonthlySnapshotTarget(
            target_month=month,
            anchor_date=f"{month.replace('-', '')}15",
            selected_trading_date=selected_date,
            selection_reason="fixture",
            status="AVAILABLE",
            regime_label=regime,
            regime_status="KNOWN",
            representative_sample_member=True,
        )
        for month, selected_date, regime, _band, _reason in FROZEN_REPRESENTATIVE_SAMPLE
    )
    selected = select_representative_snapshot_targets(targets, strict=False)
    assert [target.target_month for target in selected] == [
        item[0] for item in FROZEN_REPRESENTATIVE_SAMPLE
    ]
    assert [target.selected_trading_date for target in selected] == [
        item[1] for item in FROZEN_REPRESENTATIVE_SAMPLE
    ]
    contract = representative_sample_contract()
    assert contract["contract_version"] == REPRESENTATIVE_SAMPLE_CONTRACT_VERSION
    assert contract["selection_rule_version"] == REPRESENTATIVE_SAMPLE_RULE_VERSION
    assert len(contract["frozen_members"]) == 11
    config_file = json.loads(
        (Path(__file__).parents[1] / "docs" / "pit-replay-validation-sample-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert config_file["frozen_members"] == contract["frozen_members"]
    assert config_file["history_bands"] == contract["history_bands"]
    assert MANUAL_REVIEW_SAMPLE_MONTHS == ("2019-03", "2022-05", "2025-06")


def test_schedule_layer_is_non_replaying_and_has_stable_digest() -> None:
    result = run_replay_validation_frames(
        _validation_frames(),
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        stage="schedule",
        determinism_sample=0,
    )
    assert result.status == "SCHEDULE_READY"
    assert result.gate_status == "SCHEDULE_READY"
    assert result.snapshots == ()
    assert result.summary["monthly_target_schedule_count"] == 1
    assert result.summary["monthly_target_schedule_digest"] == monthly_target_schedule_digest(
        result.targets
    )
    assert result.targets[0].regime_label_version == MARKET_REGIME_CONTRACT_VERSION
    assert result.configuration["validation_layers"]["monthly_target_schedule"][
        "full_artifact_required"
    ] is False


def test_historical_cutoff_default_is_frozen_and_feature_as_of_stays_selected_date() -> None:
    result = run_replay_validation_frames(
        _validation_frames(),
        start="2025-06",
        end="2025-06",
        top_n=3,
        stage="monthly",
        determinism_sample=0,
    )

    assert result.configuration["today"] == "20260830"
    assert result.configuration["target_selection"]["cutoff_source"] == (
        "explicit_validation_cutoff"
    )
    assert result.targets[0].incomplete_month is False
    assert result.targets[0].selected_trading_date == "20250616"
    assert result.snapshots[0].result is not None
    assert result.snapshots[0].result.as_of_date == "20250616"


def test_historical_universe_ignores_unsafe_current_reference_fields() -> None:
    stock_basic = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH", "600002.SH", "430001.BJ"],
            "name": ["ST Current", "delisted", "future", "BSE"],
            "list_status": ["L", "D", "P", "L"],
            "industry": ["changed", "changed", "changed", "changed"],
            "market": ["主板", "主板", "主板", "BSE"],
            "list_date": ["20100101", "20100101", "20260101", "20100101"],
            "delist_date": [None, "20250629", None, None],
        }
    )
    basic = pd.DataFrame(
        {
            "ts_code": stock_basic["ts_code"],
            "trade_date": ["20250630"] * 4,
            "amount": [100.0] * 4,
        }
    )

    result = build_investable_universe(
        stock_basic,
        as_of_date="20250630",
        daily_basic=basic,
        config=UniverseConfig(
            min_financial_periods=0,
            pit_safe_only=True,
            include_bse=False,
        ),
    )

    assert set(result.included["ts_code"]) == {"600000.SH"}
    assert {item.reason for item in result.excluded} == {
        "delisted_by_as_of",
        "listed_after_as_of",
        "bse_excluded_by_identifier_policy",
    }
    assert "name" not in result.included.columns
    assert "industry" not in result.included.columns
    assert result.source_evidence["safe_fields"] == ["ts_code", "list_date", "delist_date"]
    assert "status_fields_consulted" not in result.source_evidence


def test_regime_rule_is_as_of_only_and_versioned() -> None:
    calendar = _calendar("2025-01-02", "2025-06-30")
    dates = calendar["cal_date"].tolist()
    for closes, expected in (
        ([100.0 + index * 2.0 for index in range(len(dates))], "bull"),
        ([300.0 - index * 2.0 for index in range(len(dates))], "bear"),
        ([100.0] * len(dates), "range"),
    ):
        index_daily = pd.DataFrame(
            {"ts_code": [BENCHMARK] * len(dates), "trade_date": dates, "close": closes}
        )
        regime = classify_market_regime(
            index_daily,
            dates[-1],
            trade_calendar=calendar,
            lookback_sessions=20,
        )
        assert regime.label == expected
        assert regime.contract_version == MARKET_REGIME_CONTRACT_VERSION
        assert regime.endpoint_date == dates[-1]


def test_synthetic_adversarial_matrix_passes() -> None:
    result = run_adversarial_fixtures()

    assert result["status"] == "PASS"
    assert {value["status"] for value in result["fixtures"].values()} == {"PASS"}
    assert "forward" in result["scope"]


def test_validation_uses_production_replay_and_writes_complete_audit_artifacts(tmp_path) -> None:
    frames = _validation_frames()
    result = run_replay_validation_frames(
        frames,
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        determinism_sample=1,
    )

    assert result.contract_version == PIT_REPLAY_VALIDATION_CONTRACT_VERSION
    assert result.selection_rule == MONTHLY_SELECTION_RULE_VERSION
    assert result.summary["pit_violation_count"] == 0
    assert result.summary["failed_count"] == 0
    assert result.summary["ready_count"] == 1
    assert result.summary["determinism_failure_count"] == 0
    assert result.configuration["resource_gate"]["version"] == RESOURCE_GATE_CONTRACT_VERSION
    assert result.configuration["resource_gate"]["pressure_contract_version"] == (
        RESOURCE_PRESSURE_CONTRACT_VERSION
    )
    assert result.configuration["resource_gate"]["sampling_contract_version"] == (
        RESOURCE_SAMPLING_CONTRACT_VERSION
    )
    assert "peak_rss_diagnostic_bytes" in result.summary["resource"]
    assert "current_pss_bytes" in result.summary["resource"]
    snapshot = result.snapshots[0]
    assert snapshot.result is not None
    assert snapshot.result.universe_pit_safe is True
    assert snapshot.result.universe_version == HISTORICAL_UNIVERSE_CONTRACT_VERSION
    assert snapshot.result.universe_decisions
    assert snapshot.run_manifest["validation_cutoff"] == "20251231"
    assert snapshot.run_manifest["resource_gate"]["version"] == RESOURCE_GATE_CONTRACT_VERSION
    assert snapshot.run_manifest["resource_status"] == "PASS"
    assert snapshot.run_manifest["resource_warnings"] == []
    assert result.resource_status == "NOT_ENFORCED"
    assert result.resource_warnings == ()
    assert snapshot.result.vectors
    assert snapshot.result.scores
    assert snapshot.result.full_ranked is snapshot.result.diagnostic_ranked
    assert result.manual_review["review_count"] == 1

    paths = write_replay_validation_artifacts(result, tmp_path / "artifacts")
    assert set(paths) >= {
        "manifest",
        "target_schedule",
        "target_schedule_csv",
        "summary",
        "manual_review",
        "snapshots",
    }
    schedule = json.loads(paths["target_schedule"].read_text(encoding="utf-8"))
    assert schedule["contract_version"] == MONTHLY_TARGET_SCHEDULE_CONTRACT_VERSION
    assert schedule["schedule_digest"] == result.summary["monthly_target_schedule_digest"]
    assert paths["target_schedule_csv"].read_text(encoding="utf-8").splitlines()[0].startswith(
        "target_month,anchor_date,selected_trading_date,availability_status"
    )
    machine = json.loads(paths["summary"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["validation_cutoff"] == "20251231"
    assert manifest["resource_gate"]["version"] == RESOURCE_GATE_CONTRACT_VERSION
    assert manifest["resource_status"] == "NOT_ENFORCED"
    assert manifest["resource_warnings"] == []
    assert machine["summary"]["ranking_eligible_count"] >= 0
    snapshot_files = list((tmp_path / "artifacts" / "snapshots").glob("*.json"))
    assert len(snapshot_files) == 1
    snapshot_payload = json.loads(snapshot_files[0].read_text(encoding="utf-8"))
    assert snapshot_payload["replay"]["vectors"]
    assert snapshot_payload["replay"]["universe"]["decisions"]
    assert "feature_group_registry_version" in snapshot_payload["replay"]["scores"][0]


def test_controlled_replay_normalized_snapshot_is_fully_equivalent() -> None:
    result = run_replay_validation_frames(
        _validation_frames(),
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        determinism_sample=0,
    )
    snapshot = result.snapshots[0]
    assert snapshot.result is not None
    legacy = snapshot.as_dict()
    normalized = snapshot.normalized_dict()

    assert canonical_json_bytes(expand_normalized_snapshot(normalized)) == (
        canonical_json_bytes(legacy)
    )
    assert validate_normalized_snapshot_pit(normalized, as_of_date="20250616") == ()
    assert normalized["replay"]["vectors"][0]["evidence"]


def test_controlled_two_candidate_replay_roundtrips_normalized_snapshot() -> None:
    frames = _validation_frames()
    second_code = "600001.SH"
    for dataset in (
        "stock_basic",
        "daily",
        "daily_basic",
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
    ):
        frame = frames[dataset]
        duplicate = frame.loc[frame["ts_code"].astype(str).eq(CODE)].copy()
        duplicate["ts_code"] = second_code
        frames[dataset] = pd.concat([frame, duplicate], ignore_index=True)

    result = run_replay_validation_frames(
        frames,
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        determinism_sample=0,
    )
    snapshot = result.snapshots[0]
    assert snapshot.result is not None
    assert len(snapshot.result.vectors) == 2
    normalized = snapshot.normalized_dict()

    assert canonical_json_bytes(expand_normalized_snapshot(normalized)) == (
        canonical_json_bytes(snapshot.as_dict())
    )
    assert validate_normalized_snapshot_pit(normalized, as_of_date="20250616") == ()


def test_bounded_diagnostics_are_not_a_validation_pass() -> None:
    diagnostics = ReplayDiagnostics(candidate_limit=1, checkpoint_every=1)
    result = run_replay_validation_frames(
        _validation_frames(),
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        determinism_sample=0,
        diagnostics=diagnostics,
    )

    snapshot = result.snapshots[0]
    assert result.status == "INCOMPLETE"
    assert snapshot.status == "INCOMPLETE"
    assert snapshot.result is not None
    assert snapshot.result.status == "DIAGNOSTIC_PARTIAL"
    assert diagnostics.candidate_processed == 1
    assert diagnostics.candidate_total == 1
    assert "candidate.fundamental" in diagnostics.summary()["phases"]
    assert "candidate_limit" not in snapshot.result.metadata()


def test_streaming_validation_writes_full_snapshot_before_releasing_result(
    tmp_path, monkeypatch
) -> None:
    frames = _validation_frames()

    def reject_expanded_artifact(_self):
        raise AssertionError("streamed production path called ReplayResult.artifact_dict()")

    monkeypatch.setattr(ReplayResult, "artifact_dict", reject_expanded_artifact)
    output = tmp_path / "streamed"
    result = run_replay_validation_frames(
        frames,
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        determinism_sample=1,
        artifact_output=output,
    )

    assert result.status == "READY"
    assert result.snapshots[0].result is None
    snapshot_path = next((output / "snapshots").glob("*.json.gz"))
    snapshot_payload = json.loads(gzip.decompress(snapshot_path.read_bytes()))
    assert snapshot_payload["replay"]["vectors"]
    assert snapshot_payload["replay"]["diagnostic_ranked"]
    assert snapshot_payload["replay"]["scores"][0]["evidence_confidence_contract_version"]
    checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "COMPLETE"
    assert checkpoint["completed"][0]["status"] == "READY"
    assert result.summary["determinism_failure_count"] == 0
    stages = [sample["stage"] for sample in result.summary["resource"]["samples"]]
    required_order = [
        "before_replay_frames_release",
        "after_replay_frames_release",
        "before_cas_finalize",
        "after_cas_finalize",
        "before_artifact_writer",
        "after_artifact_writer_and_cleanup",
    ]
    assert [stages.index(stage) for stage in required_order] == sorted(
        stages.index(stage) for stage in required_order
    )
    cas = result.summary["resource"]["cas_finalization"]["2025-06"]
    assert cas["finalization_count"] == 1
    assert cas["configured_merge_fan_in"] == 32
    assert cas["peak_open_chunk_streams"] <= 32


def test_data_directory_validation_projects_only_as_of_inputs(tmp_path) -> None:
    frames = _validation_frames()
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    for dataset, frame in frames.items():
        if frame.empty:
            continue
        if dataset in {"trade_cal", "daily", "daily_basic", "index_daily"}:
            store.write(dataset, frame)
        elif dataset in {"income", "balancesheet", "cashflow", "fina_indicator"}:
            store.write(dataset, frame)
        else:
            store.write(dataset, frame)

    result = run_replay_validation(
        data_dir,
        start="2025-06",
        end="2025-06",
        today="2025-12-31",
        top_n=3,
        stage="monthly",
        content_hash=False,
        determinism_sample=0,
    )

    assert result.summary["failed_count"] == 0
    assert result.summary["pit_violation_count"] == 0
    assert result.snapshots[0].result is not None
    assert result.snapshots[0].result.input_rows["daily"] < len(frames["daily"])
    assert result.snapshots[0].result.input_rows["daily_basic"] < len(frames["daily_basic"])


def test_manifest_identity_is_repeatable_and_tracks_partition_metadata(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    store.write(
        "daily",
        pd.DataFrame(
            {
                "ts_code": [CODE],
                "trade_date": ["20250630"],
                "close": [10.0],
            }
        ),
    )

    first = build_input_manifest(tmp_path / "data", content_hash=False)
    second = build_input_manifest(tmp_path / "data", content_hash=False)

    assert first["manifest_id"] == second["manifest_id"]
    assert first["dataset_manifest_ids"] == second["dataset_manifest_ids"]
    entry = first["datasets"]["daily"]["files"][0]
    assert entry["rows"] == 1
    assert entry["schema_hash"]
    assert entry["content_hash"] is None


def test_large_corpus_resource_gate_blocks_low_available_ram(monkeypatch, tmp_path) -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    monkeypatch.setattr(validation, "_raw_corpus_bytes", lambda _: validation.LARGE_CORPUS_BYTES)
    monkeypatch.setattr(
        validation,
        "_host_memory",
        lambda: {
            "available_bytes": 1 * 1024**3,
            "swap_total_bytes": 4 * 1024**3,
            "swap_free_bytes": 3 * 1024**3,
            "swap_used_bytes": 1 * 1024**3,
            "current_rss_bytes": 2 * 1024**3,
            "current_pss_bytes": 2 * 1024**3,
            "current_private_bytes": 2 * 1024**3,
            "current_swap_bytes": 0,
            "peak_rss_diagnostic_bytes": 0,
        },
    )

    with pytest.raises(ResourceBlocked, match="available RAM"):
        validation._assert_initial_resource_gate(tmp_path)


def _healthy_resource_sample(**overrides):
    sample = {
        "available_bytes": 8 * 1024**3,
        "swap_total_bytes": 4 * 1024**3,
        "swap_free_bytes": 3 * 1024**3,
        "swap_used_bytes": 1 * 1024**3,
        "current_rss_bytes": 2 * 1024**3,
        "current_pss_bytes": 2 * 1024**3,
        "current_private_bytes": 2 * 1024**3,
        "current_swap_bytes": 0,
        "peak_rss_diagnostic_bytes": int(6.1 * 1024**3),
        "peak_rss_bytes": int(6.1 * 1024**3),
        "live_memory_metric": "proc_smaps_rollup",
    }
    sample.update(overrides)
    return sample


def test_resource_gate_healthy_no_swap_is_pass() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    checked = validation._assert_runtime_resource_gate(
        {"swap_used_bytes": 0},
        memory=_healthy_resource_sample(
            swap_total_bytes=0,
            swap_free_bytes=0,
            swap_used_bytes=0,
            current_swap_bytes=0,
        ),
    )

    assert checked["resource_status"] == "PASS"
    assert checked["resource_warnings"] == []


def test_resource_gate_treats_cold_swapped_pages_as_warning() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    checked = validation._assert_runtime_resource_gate(
        {"swap_used_bytes": 1 * 1024**3},
        memory=_healthy_resource_sample(
            swap_free_bytes=400 * 1024**2,
            swap_used_bytes=1 * 1024**3 + validation.MAX_SWAP_GROWTH_BYTES + 1,
            current_swap_bytes=validation.MAX_PROCESS_SWAP_BYTES + 1,
        ),
    )

    assert checked["resource_status"] == "PASS_WITH_WARNING"
    assert checked["resource_warnings"] == [
        RESOURCE_WARNING_PROCESS_SWAP,
        RESOURCE_WARNING_SWAP_FREE,
        RESOURCE_WARNING_SWAP_GROWTH,
    ]
    assert checked["swap_pressure_active"] is False


def test_resource_gate_ignores_ru_maxrss_when_live_working_set_is_healthy(monkeypatch) -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    monkeypatch.setattr(validation, "_host_memory", lambda: _healthy_resource_sample())
    validation._assert_runtime_resource_gate({"swap_used_bytes": 1 * 1024**3})


def test_resource_gate_blocks_live_pss_over_six_gib(monkeypatch) -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    monkeypatch.setattr(
        validation,
        "_host_memory",
        lambda: _healthy_resource_sample(
            current_pss_bytes=validation.MAX_LIVE_PSS_BYTES + 1,
            current_private_bytes=2 * 1024**3,
        ),
    )
    with pytest.raises(ResourceBlocked, match="live PSS"):
        validation._assert_runtime_resource_gate({"swap_used_bytes": 1 * 1024**3})


def test_resource_gate_blocks_active_swap_thrashing() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    baseline = _healthy_resource_sample(
        sampled_monotonic=0.0,
        pswpin_pages=100,
        pswpout_pages=100,
        swap_page_size_bytes=4096,
    )
    observed = _healthy_resource_sample(
        available_bytes=3 * 1024**3,
        sampled_monotonic=30.0,
        pswpin_pages=12_100,
        pswpout_pages=12_100,
        swap_page_size_bytes=4096,
    )

    with pytest.raises(ResourceBlocked, match="active swap thrashing"):
        validation._assert_runtime_resource_gate(
            baseline,
            memory=observed,
            phase="synthetic-thrashing",
            sample_history=[baseline, observed],
            require_complete_telemetry=True,
        )


def test_resource_gate_fails_closed_when_large_run_telemetry_is_unavailable() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    with pytest.raises(ResourceBlocked, match="resource sampler unavailable"):
        validation._assert_runtime_resource_gate(
            {},
            memory=_healthy_resource_sample(
                pswpin_pages=None,
                pswpout_pages=None,
                swap_io_supported=False,
            ),
            require_complete_telemetry=True,
        )


def test_resource_gate_warns_system_swap_growth_over_soft_limit() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    observed = _healthy_resource_sample(
        swap_used_bytes=1 * 1024**3 + validation.MAX_SWAP_GROWTH_BYTES + 1,
    )
    checked = validation._assert_runtime_resource_gate(
        {"swap_used_bytes": 1 * 1024**3},
        memory=observed,
    )

    assert checked["resource_status"] == "PASS_WITH_WARNING"
    assert checked["resource_warnings"] == [RESOURCE_WARNING_SWAP_GROWTH]


def test_resource_gate_warns_process_swap_over_soft_limit() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    checked = validation._assert_runtime_resource_gate(
        {"swap_used_bytes": 1 * 1024**3},
        memory=_healthy_resource_sample(
            current_swap_bytes=validation.MAX_PROCESS_SWAP_BYTES + 1,
        ),
    )

    assert checked["resource_status"] == "PASS_WITH_WARNING"
    assert checked["resource_warnings"] == [RESOURCE_WARNING_PROCESS_SWAP]


def test_resource_warnings_are_persisted_in_decision_manifest_and_report(
    tmp_path, monkeypatch
) -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    resource_sample = _healthy_resource_sample(
        swap_free_bytes=400 * 1024**2,
        current_swap_bytes=validation.MAX_PROCESS_SWAP_BYTES + 1,
        pswpin_pages=100,
        pswpout_pages=100,
        swap_io_supported=True,
        swap_page_size_bytes=4096,
        sampled_monotonic=0.0,
    )
    monkeypatch.setattr(validation, "_host_memory", lambda: dict(resource_sample))
    frames = _validation_frames()
    result = validation._run_validation(
        frames,
        data_dir="<in-memory>",
        input_manifest=validation._frame_manifest(frames),
        start="2025-06",
        end="2025-06",
        selection_rule=validation.MONTHLY_SELECTION_RULE_VERSION,
        anchor_day=15,
        calendar_exchange="SSE",
        top_n=3,
        replay_config=ReplayConfig(top_n=3),
        seed=0,
        stage="monthly",
        today="2025-12-31",
        determinism_sample=0,
        resource_guard=True,
    )

    assert result.status == "READY"
    assert result.resource_status == "PASS_WITH_WARNING"
    assert result.resource_warnings == (
        RESOURCE_WARNING_PROCESS_SWAP,
        RESOURCE_WARNING_SWAP_FREE,
    )
    assert result.summary["resource"]["warnings"] == list(result.resource_warnings)
    assert result.summary["resource"]["samples"]
    assert result.snapshots[0].run_manifest["resource_warnings"] == list(result.resource_warnings)

    paths = validation.write_replay_validation_artifacts(result, tmp_path / "artifacts")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    markdown = paths["summary_markdown"].read_text(encoding="utf-8")
    assert manifest["resource_status"] == "PASS_WITH_WARNING"
    assert manifest["resource_warnings"] == list(result.resource_warnings)
    assert summary["summary"]["resource"]["warnings"] == list(result.resource_warnings)
    assert all(
        sample["resource_warnings"] for sample in summary["summary"]["resource"]["samples"]
    )
    assert "historical_process_swap_above_soft_limit" in markdown


def test_resource_parser_and_report_keep_peak_rss_as_diagnostic() -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    parsed = validation._parse_proc_memory_text(
        """Rss: 10 kB
Pss: 8 kB
Private_Clean: 2 kB
VmSwap: 3 kB"""
    )
    assert parsed == {
        "Rss": 10 * 1024,
        "Pss": 8 * 1024,
        "Private_Clean": 2 * 1024,
        "VmSwap": 3 * 1024,
    }
    assert validation._parse_proc_vmstat_text("pswpin 12\npswpout 34\nbad value") == {
        "pswpin": 12,
        "pswpout": 34,
    }
    telemetry = validation._host_memory()
    assert "peak_rss_diagnostic_bytes" in telemetry
    assert "current_pss_bytes" in telemetry
    assert "current_private_bytes" in telemetry


def test_resource_telemetry_uses_current_vmrss_when_smaps_rollup_is_unavailable(
    monkeypatch,
) -> None:
    import ashare_turnaround.scanner.replay_validation as validation

    def fake_read(path):
        path = str(path)
        if path == "/proc/meminfo":
            return {
                "MemAvailable": 8 * 1024**3,
                "SwapTotal": 4 * 1024**3,
                "SwapFree": 3 * 1024**3,
            }
        if path == "/proc/self/smaps_rollup":
            raise OSError("not supported")
        if path == "/proc/self/status":
            return {"VmRSS": 2 * 1024**3, "VmSwap": 7 * 1024**2}
        raise AssertionError(path)

    monkeypatch.setattr(validation, "_read_proc_memory", fake_read)
    monkeypatch.setattr(
        validation.resource,
        "getrusage",
        lambda _: type("Usage", (), {"ru_maxrss": int(6.1 * 1024**3 / 1024)})(),
    )
    telemetry = validation._host_memory()

    assert telemetry["current_rss_bytes"] == 2 * 1024**3
    assert telemetry["current_pss_bytes"] is None
    assert telemetry["current_private_bytes"] is None
    assert telemetry["current_swap_bytes"] == 7 * 1024**2
    assert telemetry["peak_rss_diagnostic_bytes"] > validation.MAX_LIVE_PSS_BYTES
    assert telemetry["live_memory_metric"] == "proc_status_vmrss_fallback"


def test_pit_validator_detects_future_evidence_as_hard_violation() -> None:
    frames = _validation_frames()
    result = (
        run_replay_validation_frames(
            frames,
            start="2025-06",
            end="2025-06",
            today="2025-12-31",
            stage="monthly",
            determinism_sample=0,
        )
        .snapshots[0]
        .result
    )
    assert result is not None
    vector = result.vectors[0]
    vector.add(
        "future_probe",
        1.0,
        availability_dates=("20250701",),
        metadata={"observation_date": "20250701"},
    )
    violations = validate_replay_pit(
        result,
        as_of_date="20250616",
        require_historical_universe=True,
    )
    assert any("observation_after_as_of" in violation for violation in violations)
