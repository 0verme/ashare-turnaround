"""Small, explicit specifications for the Phase 1 datasets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    name: str
    api_name: str
    primary_keys: tuple[str, ...]
    partition_strategy: str = "none"
    partition_field: str | None = None
    date_fields: tuple[str, ...] = ()
    pit_fields: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()


API_VALIDATION_ORDER = (
    "stock_basic",
    "trade_cal",
    "daily",
    "daily_basic",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "fina_mainbz",
    "forecast",
    "express",
    "fina_audit",
    "disclosure_date",
)

CORE_DATASETS = (
    "daily_basic",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)

VIP_API_NAMES = (
    "income_vip",
    "balancesheet_vip",
    "cashflow_vip",
    "fina_indicator_vip",
    "fina_mainbz_vip",
    "forecast_vip",
    "express_vip",
)


def _spec(
    name: str,
    *,
    primary_keys: tuple[str, ...],
    partition_strategy: str = "none",
    partition_field: str | None = None,
    date_fields: tuple[str, ...] = (),
    pit_fields: tuple[str, ...] = (),
    required_fields: tuple[str, ...] = (),
) -> DatasetSpec:
    return DatasetSpec(
        name=name,
        api_name=name,
        primary_keys=primary_keys,
        partition_strategy=partition_strategy,
        partition_field=partition_field,
        date_fields=date_fields,
        pit_fields=pit_fields,
        required_fields=required_fields,
    )


DATASET_SPECS: dict[str, DatasetSpec] = {
    "stock_basic": _spec(
        "stock_basic",
        primary_keys=("ts_code",),
        required_fields=("ts_code", "symbol", "name"),
    ),
    "trade_cal": _spec(
        "trade_cal",
        primary_keys=("exchange", "cal_date"),
        partition_strategy="year",
        partition_field="cal_date",
        date_fields=("cal_date",),
        required_fields=("exchange", "cal_date", "is_open"),
    ),
    "daily": _spec(
        "daily",
        primary_keys=("ts_code", "trade_date"),
        partition_strategy="date",
        partition_field="trade_date",
        date_fields=("trade_date",),
        required_fields=("ts_code", "trade_date", "close"),
    ),
    "daily_basic": _spec(
        "daily_basic",
        primary_keys=("ts_code", "trade_date"),
        partition_strategy="date",
        partition_field="trade_date",
        date_fields=("trade_date",),
        required_fields=("ts_code", "trade_date"),
    ),
    "income": _spec(
        "income",
        primary_keys=(
            "ts_code",
            "end_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        ),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "f_ann_date", "end_date"),
        pit_fields=("end_date", "ann_date", "f_ann_date", "report_type", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "report_type", "update_flag"),
    ),
    "balancesheet": _spec(
        "balancesheet",
        primary_keys=(
            "ts_code",
            "end_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        ),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "f_ann_date", "end_date"),
        pit_fields=("end_date", "ann_date", "f_ann_date", "report_type", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "report_type", "update_flag"),
    ),
    "cashflow": _spec(
        "cashflow",
        primary_keys=(
            "ts_code",
            "end_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        ),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "f_ann_date", "end_date"),
        pit_fields=("end_date", "ann_date", "f_ann_date", "report_type", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "report_type", "update_flag"),
    ),
    "fina_indicator": _spec(
        "fina_indicator",
        primary_keys=("ts_code", "end_date", "update_flag"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "end_date"),
        pit_fields=("end_date", "ann_date", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "update_flag"),
    ),
    "fina_mainbz": _spec(
        "fina_mainbz",
        primary_keys=("ts_code", "end_date", "bz_item", "curr_type", "update_flag"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("end_date",),
        pit_fields=("end_date", "update_flag"),
        required_fields=("ts_code", "end_date", "bz_item", "update_flag"),
    ),
    "forecast": _spec(
        "forecast",
        primary_keys=("ts_code", "end_date", "type", "ann_date", "update_flag"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "end_date", "first_ann_date"),
        pit_fields=("end_date", "ann_date", "first_ann_date", "type", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "type", "update_flag"),
    ),
    "express": _spec(
        "express",
        primary_keys=("ts_code", "end_date", "ann_date", "update_flag"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "end_date"),
        pit_fields=("end_date", "ann_date", "update_flag"),
        required_fields=("ts_code", "ann_date", "end_date", "update_flag"),
    ),
    "fina_audit": _spec(
        "fina_audit",
        primary_keys=("ts_code", "end_date", "ann_date"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "end_date"),
        pit_fields=("end_date", "ann_date"),
        required_fields=("ts_code", "ann_date", "end_date"),
    ),
    "disclosure_date": _spec(
        "disclosure_date",
        primary_keys=("ts_code", "end_date"),
        partition_strategy="year",
        partition_field="end_date",
        date_fields=("ann_date", "end_date", "pre_date", "actual_date", "modify_date"),
        pit_fields=("end_date", "ann_date", "actual_date", "modify_date"),
        required_fields=("ts_code", "end_date", "pre_date", "actual_date"),
    ),
}


def get_dataset_spec(name: str) -> DatasetSpec:
    """Return a spec for a base or explicitly named VIP API."""

    if name in DATASET_SPECS:
        return DATASET_SPECS[name]
    if name in VIP_API_NAMES:
        base_name = name.removesuffix("_vip")
        base = DATASET_SPECS[base_name]
        return DatasetSpec(
            name=name,
            api_name=name,
            primary_keys=base.primary_keys,
            partition_strategy=base.partition_strategy,
            partition_field=base.partition_field,
            date_fields=base.date_fields,
            pit_fields=base.pit_fields,
            required_fields=base.required_fields,
        )
    raise KeyError(f"unknown dataset: {name}")
