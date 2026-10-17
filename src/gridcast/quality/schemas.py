"""Pandera schemas for silver tables (M1-T4). Every table is validated before it is written."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pandera.pandas as pa

from gridcast.timeutil.ieso import TS_DTYPE

SOURCES = ["ieso", "gridstatus"]


def _ts(nullable: bool = False) -> pa.Column:
    return pa.Column(TS_DTYPE, nullable=nullable)


def _is_date(s: pd.Series) -> bool:
    return bool(s.map(lambda v: isinstance(v, date)).all())


DELIVERY = {
    "delivery_date": pa.Column(checks=pa.Check(_is_date, element_wise=False, name="is_date")),
    "hour_ending": pa.Column("int8", checks=pa.Check.in_range(1, 24)),
}
PRICE = pa.Column("float64", nullable=False)
COMPONENT = pa.Column("float64", nullable=True)
SOURCE = pa.Column(checks=pa.Check.isin(SOURCES))
VERSION = pa.Column("Int16", nullable=True, checks=pa.Check.ge(1))
SHA = pa.Column(checks=pa.Check.str_matches(r"^[0-9a-f]{64}$"))
STR_OPT = pa.Column(nullable=True)

DA_OZP = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "zonal_price": PRICE,
        "loss_price_capped": COMPONENT,
        "congestion_price_capped": COMPONENT,
        "flag": STR_OPT,
        "version": VERSION,
        "source": SOURCE,
        "doc_created_at": _ts(nullable=True),
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc", "source", "version"],
    name="da_ozp",
)

PREDISP_OZP = pa.DataFrameSchema(
    {
        "issued_at_utc": _ts(),
        "target_ts_utc": _ts(),
        **DELIVERY,
        "zonal_price": PRICE,
        "loss_price_capped": COMPONENT,
        "congestion_price_capped": COMPONENT,
        "version": VERSION,
        "source": SOURCE,
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["target_ts_utc", "issued_at_utc", "source", "version"],
    name="predisp_ozp",
)

RT_OZP_5MIN = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "interval": pa.Column("int8", checks=pa.Check.in_range(1, 12)),
        "lmp": PRICE,
        "loss_price_capped": COMPONENT,
        "congestion_price_capped": COMPONENT,
        "flag": STR_OPT,
        "version": VERSION,
        "source": SOURCE,
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc", "source"],
    name="rt_ozp_5min",
)

RT_OZP_HOURLY = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "lmp_mean": PRICE,
        "n_intervals": pa.Column("int8", checks=pa.Check.in_range(1, 12)),
        "source": SOURCE,
        "available_at_utc": _ts(),
    },
    strict=True,
    unique=["ts_utc", "source"],
    name="rt_ozp_hourly",
)

DEMAND = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "market_demand": pa.Column("float64", checks=pa.Check.gt(0)),
        "ontario_demand": pa.Column("float64", checks=pa.Check.gt(0)),
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc"],
    name="demand",
)

HOEP = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "hoep": pa.Column("float64", nullable=True),
        "predispatch_1h": pa.Column("float64", nullable=True),
        "predispatch_2h": pa.Column("float64", nullable=True),
        "predispatch_3h": pa.Column("float64", nullable=True),
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc"],
    name="hoep",
)

GEN_BY_FUEL = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "fuel": pa.Column(
            checks=pa.Check.isin(
                ["biofuel", "gas", "hydro", "nuclear", "other", "solar", "wind", "storage"]
            )
        ),
        "output_mw": pa.Column("float64", nullable=True),
        "capability_mw": pa.Column("float64", nullable=True),
        "available_capacity_mw": pa.Column("float64", nullable=True),
        "forecast_mw": pa.Column("float64", nullable=True),
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc", "fuel"],
    name="gen_by_fuel",
)

INTERTIE = pa.DataFrameSchema(
    {
        "ts_utc": _ts(),
        **DELIVERY,
        "imports_sched_mw": pa.Column("float64", nullable=True),
        "exports_sched_mw": pa.Column("float64", nullable=True),
        "total_flow_mw": pa.Column("float64", nullable=True),
        "available_at_utc": _ts(),
        "source_sha256": SHA,
    },
    strict=True,
    unique=["ts_utc"],
    name="intertie",
)

WEATHER_VARS = [
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_100m",
    "cloud_cover",
    "shortwave_radiation",
]

WEATHER_FCST = pa.DataFrameSchema(
    {
        "point": pa.Column(),
        "valid_ts_utc": _ts(),
        "lead_days": pa.Column("int8", checks=pa.Check.in_range(1, 7)),
        **{v: pa.Column("float64", nullable=True) for v in WEATHER_VARS},
        "model": pa.Column(),
        "available_at_utc": _ts(),
        "fetched_at_utc": _ts(),
    },
    strict=True,
    unique=["point", "valid_ts_utc", "lead_days"],
    checks=[
        pa.Check(
            lambda df: (df["available_at_utc"] <= df["fetched_at_utc"]).all(),
            name="genuine_previous_runs_only",
        )
    ],
    name="weather_fcst",
)


def forecast_schema(name: str, value_columns: list[str]) -> pa.DataFrameSchema:
    """Vintaged hourly operator forecasts flattened from nested IESO reports."""
    return pa.DataFrameSchema(
        {
            "issued_at_utc": _ts(),
            "target_ts_utc": _ts(),
            **DELIVERY,
            **{c: pa.Column("float64", nullable=True) for c in value_columns},
            "version": VERSION,
            "source": SOURCE,
            "available_at_utc": _ts(),
            "source_sha256": SHA,
        },
        strict=True,
        unique=["target_ts_utc", "issued_at_utc", "version"],
        name=name,
    )


def _flat_schemas() -> list[pa.DataFrameSchema]:
    from gridcast.parse.ieso_flatten import ADEQUACY_SERIES, DA_TOTALS_SERIES, VG_SERIES

    return [
        forecast_schema("adequacy_fcst", list(ADEQUACY_SERIES)),
        forecast_schema("vg_fcst", list(VG_SERIES)),
        forecast_schema("da_totals", list(DA_TOTALS_SERIES)),
    ]


SCHEMAS = {
    s.name: s
    for s in (
        DA_OZP,
        PREDISP_OZP,
        RT_OZP_5MIN,
        RT_OZP_HOURLY,
        DEMAND,
        HOEP,
        WEATHER_FCST,
        GEN_BY_FUEL,
        INTERTIE,
        *_flat_schemas(),
    )
}
