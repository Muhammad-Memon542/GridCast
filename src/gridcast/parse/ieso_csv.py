"""Parsers for IESO yearly CSV reports (Demand, HOEP) (M1-T4)."""

from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from gridcast.timeutil.ieso import ieso_naive_to_utc

_CREATED = re.compile(r"Created at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def read_ieso_csv(content: bytes) -> tuple[pd.DataFrame, datetime | None]:
    """Strip the ``\\\\``-prefixed preamble; return the table and its ``Created at`` (UTC)."""
    text = content.decode("utf-8-sig")
    preamble = [line for line in text.splitlines() if line.startswith("\\")]
    created = None
    for line in preamble:
        if match := _CREATED.search(line):
            created = ieso_naive_to_utc(datetime.fromisoformat(match.group(1)))
    body = "\n".join(line for line in text.splitlines() if not line.startswith("\\"))
    frame = pd.read_csv(io.StringIO(body))
    frame["Date"] = pd.to_datetime(frame["Date"], format="%Y-%m-%d").dt.date
    return frame, created


def parse_demand(content: bytes) -> pd.DataFrame:
    frame, created = read_ieso_csv(content)
    out = pd.DataFrame(
        {
            "delivery_date": frame["Date"],
            "hour_ending": frame["Hour"].astype("int8"),
            "market_demand": frame["Market Demand"].astype("float64"),
            "ontario_demand": frame["Ontario Demand"].astype("float64"),
        }
    )
    out["doc_created_at"] = created
    return out


def parse_hoep(content: bytes) -> pd.DataFrame:
    frame, created = read_ieso_csv(content)
    out = pd.DataFrame(
        {
            "delivery_date": frame["Date"],
            "hour_ending": frame["Hour"].astype("int8"),
            "hoep": pd.to_numeric(frame["HOEP"], errors="coerce"),
            "predispatch_1h": pd.to_numeric(frame["Hour 1 Predispatch"], errors="coerce"),
            "predispatch_2h": pd.to_numeric(frame["Hour 2 Predispatch"], errors="coerce"),
            "predispatch_3h": pd.to_numeric(frame["Hour 3 Predispatch"], errors="coerce"),
        }
    )
    out["doc_created_at"] = created
    return out


GEN_MEASURES = {
    "Output": "output_mw",
    "Capability": "capability_mw",
    "Available Capacity": "available_capacity_mw",
    "Forecast": "forecast_mw",
}


def parse_gen_month(content: bytes) -> pd.DataFrame:
    """``GenOutputCapabilityMonth``: unit-level hourly values summed by fuel and measurement."""
    frame, created = read_ieso_csv_with(content, date_column="Delivery Date")
    hours = [c for c in frame.columns if str(c).startswith("Hour ")]
    long = frame.melt(
        id_vars=["Delivery Date", "Fuel Type", "Measurement"],
        value_vars=hours,
        var_name="hour",
        value_name="mw",
    )
    long["mw"] = pd.to_numeric(long["mw"], errors="coerce")
    long["hour_ending"] = long["hour"].str.removeprefix("Hour ").astype("int8")
    long = long[long["Measurement"].isin(GEN_MEASURES)]
    agg = (
        long.groupby(["Delivery Date", "hour_ending", "Fuel Type", "Measurement"], sort=True)["mw"]
        .sum(min_count=1)
        .unstack("Measurement")
        .reindex(columns=list(GEN_MEASURES))
        .rename(columns=GEN_MEASURES)
        .reset_index()
        .rename(columns={"Delivery Date": "delivery_date", "Fuel Type": "fuel"})
    )
    agg.columns.name = None
    agg["fuel"] = agg["fuel"].str.lower()
    agg["doc_created_at"] = created
    return agg


def parse_intertie_year(content: bytes) -> pd.DataFrame:
    """``IntertieScheduleFlowYear``: Ontario totals of scheduled imports/exports and actual flow.

    The table has two header rows (intertie zone, then Imp/Exp/Flow). A positive flow is
    out of Ontario (e.g. 2025-01-01 HE1: Manitoba import 85 MW, flow -86 MW).
    """
    text = content.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if not line.startswith("\\")]
    created = None
    for line in text.splitlines():
        if match := _CREATED.search(line):
            created = ieso_naive_to_utc(datetime.fromisoformat(match.group(1)))
    zones, fields = lines[0].split(","), lines[1].split(",")
    names = [
        f"{z.strip()}|{f.strip()}" if z.strip() else f.strip()
        for z, f in zip(zones, fields, strict=True)
    ]
    frame = pd.read_csv(
        io.StringIO("\n".join(lines[2:])), header=None, names=names, index_col=False
    )
    out = pd.DataFrame(
        {
            "delivery_date": pd.to_datetime(frame["Date"], format="%Y-%m-%d").dt.date,
            "hour_ending": frame["Hour"].astype("int8"),
            "imports_sched_mw": pd.to_numeric(frame["Total|Imp"], errors="coerce"),
            "exports_sched_mw": pd.to_numeric(frame["Total|Exp"], errors="coerce"),
            "total_flow_mw": pd.to_numeric(frame["Total|Flow"], errors="coerce"),
        }
    )
    out["doc_created_at"] = created
    return out


def read_ieso_csv_with(content: bytes, date_column: str) -> tuple[pd.DataFrame, datetime | None]:
    """Like :func:`read_ieso_csv` for reports whose date column is not called ``Date``."""
    text = content.decode("utf-8-sig")
    created = None
    for line in text.splitlines():
        if line.startswith("\\") and (match := _CREATED.search(line)):
            created = ieso_naive_to_utc(datetime.fromisoformat(match.group(1)))
    body = "\n".join(line for line in text.splitlines() if not line.startswith("\\"))
    # Data rows end with a trailing comma; index_col=False stops pandas shifting columns.
    frame = pd.read_csv(io.StringIO(body), index_col=False)
    frame[date_column] = pd.to_datetime(frame[date_column], format="%Y-%m-%d").dt.date
    return frame, created
