"""Build silver tables from bronze (M1-T4): typed, UTC, every version kept, validated.

Silver is fully derived. ``build_all`` rebuilds every table from bronze deterministically
and writes ``data/silver/<table>/month=YYYY-MM/part-0.parquet``.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import structlog

from gridcast.features.availability import (
    AvailabilityConfig,
    after_end_rule,
    est_time_rule,
    ieso_file_available,
    load_availability_config,
    local_time_rule,
)
from gridcast.ingest.weather import WeatherConfig, load_weather_config
from gridcast.parse.ieso_csv import parse_demand, parse_hoep
from gridcast.parse.ieso_xml import parse_da_ozp, parse_predisp_ozp, parse_rt_ozp
from gridcast.quality.schemas import SCHEMAS, WEATHER_VARS
from gridcast.timeutil.ieso import TS_DTYPE, he_to_ts_utc, ts_to_delivery

log = structlog.get_logger(__name__)

TS_COLUMN = {
    "da_ozp": "ts_utc",
    "predisp_ozp": "target_ts_utc",
    "rt_ozp_5min": "ts_utc",
    "rt_ozp_hourly": "ts_utc",
    "demand": "ts_utc",
    "hoep": "ts_utc",
    "weather_fcst": "valid_ts_utc",
}


# ------------------------------------------------------------------ bronze access


def manifest_files(data_dir: Path, report: str) -> pd.DataFrame:
    """Stored (non-duplicate) bronze files of an IESO report, from the collector manifest."""
    path = data_dir / "bronze" / "_manifest.sqlite"
    if not path.exists():
        return pd.DataFrame(
            columns=["path", "version", "period_key", "listing", "created", "sha256"]
        )
    with sqlite3.connect(path) as db:
        frame = pd.read_sql_query(
            "SELECT path, version, period_key, listing_modified_utc AS listing,"
            " doc_created_at_utc AS created, sha256 FROM files"
            " WHERE report=? AND http_status=200 AND duplicate_of IS NULL AND path IS NOT NULL"
            " ORDER BY period_key, version",
            db,
            params=(report,),
        )
    return frame


def read_bronze(data_dir: Path, rel: str) -> bytes:
    raw = (data_dir / rel).read_bytes()
    return gzip.decompress(raw) if rel.endswith(".gz") else raw


def _parse_files(
    data_dir: Path, files: pd.DataFrame, parser: Callable[[bytes], pd.DataFrame]
) -> pd.DataFrame:
    parts = []
    for row in files.itertuples(index=False):
        part = parser(read_bronze(data_dir, str(row.path)))
        part["version"] = row.version
        part["listing"] = row.listing
        part["source_sha256"] = row.sha256
        parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _latest_version_per_key(files: pd.DataFrame) -> pd.DataFrame:
    return files.sort_values(["period_key", "version"]).drop_duplicates("period_key", keep="last")


def gridstatus_frame(data_dir: Path, dataset: str) -> tuple[pd.DataFrame, pd.Series]:
    """All pulls of a dataset, latest pull winning per row; plus each row's file sha256."""
    path = data_dir / "bronze" / "_gridstatus.sqlite"
    if not path.exists():
        return pd.DataFrame(), pd.Series(dtype="str")
    with sqlite3.connect(path) as db:
        pulls = pd.read_sql_query(
            "SELECT path, sha256 FROM pulls WHERE dataset=? ORDER BY pulled_at_utc",
            db,
            params=(dataset,),
        )
    parts = []
    for row in pulls.itertuples(index=False):
        part = pd.read_parquet(data_dir / str(row.path))
        part["_sha"] = row.sha256
        parts.append(part)
    if not parts:
        return pd.DataFrame(), pd.Series(dtype="str")
    frame = pd.concat(parts, ignore_index=True)
    frame = frame.drop_duplicates(["interval_start_utc", "location"], keep="last")
    frame = frame.sort_values("interval_start_utc").reset_index(drop=True)
    return frame.drop(columns="_sha"), frame["_sha"]


def _ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).astype(TS_DTYPE)


# ----------------------------------------------------------------------- tables


def build_da_ozp(data_dir: Path, avail: AvailabilityConfig) -> pd.DataFrame:
    ieso = _parse_files(
        data_dir, manifest_files(data_dir, "DAHourlyOntarioZonalPrice"), parse_da_ozp
    )
    frames = []
    if not ieso.empty:
        ieso["ts_utc"] = he_to_ts_utc(ieso["delivery_date"], ieso["hour_ending"])
        ieso["available_at_utc"] = ieso_file_available(
            ieso["doc_created_at"], ieso["listing"], avail.listing_margin_minutes
        )
        ieso["source"] = "ieso"
        frames.append(ieso)
    gs, sha = gridstatus_frame(data_dir, "ieso_lmp_day_ahead_hourly_ontario_zonal")
    if not gs.empty:
        ts = _ts(gs["interval_start_utc"])
        days, hours = ts_to_delivery(ts)
        frames.append(
            pd.DataFrame(
                {
                    "ts_utc": ts,
                    "delivery_date": days,
                    "hour_ending": hours,
                    "zonal_price": gs["lmp"].astype("float64"),
                    "loss_price_capped": gs["loss"].astype("float64"),
                    "congestion_price_capped": gs["congestion"].astype("float64"),
                    "flag": None,
                    "version": pd.NA,
                    "source": "gridstatus",
                    "doc_created_at": pd.NaT,
                    "available_at_utc": local_time_rule(days, avail.da_ozp_gridstatus),
                    "source_sha256": sha.to_numpy(),
                }
            )
        )
    return _finish(frames, "da_ozp")


def build_predisp_ozp(data_dir: Path, avail: AvailabilityConfig) -> pd.DataFrame:
    files = manifest_files(data_dir, "PredispHourlyOntarioZonalPrice")
    ieso = _parse_files(data_dir, files, parse_predisp_ozp)
    frames = []
    if not ieso.empty:
        ieso["target_ts_utc"] = he_to_ts_utc(ieso["delivery_date"], ieso["hour_ending"])
        ieso["issued_at_utc"] = ieso_file_available(
            ieso["doc_created_at"], ieso["listing"], avail.listing_margin_minutes
        )
        ieso["available_at_utc"] = ieso["issued_at_utc"]
        ieso["source"] = "ieso"
        frames.append(ieso)
    return _finish(frames, "predisp_ozp")


def build_rt_ozp_5min(data_dir: Path, avail: AvailabilityConfig) -> pd.DataFrame:
    files = _latest_version_per_key(manifest_files(data_dir, "RealtimeOntarioZonalPrice"))
    ieso = _parse_files(data_dir, files, parse_rt_ozp)
    frames = []
    if not ieso.empty:
        # The in-progress hour lists all 12 intervals; not-yet-published ones are empty.
        ieso = ieso[ieso["lmp"].notna()].copy()
        ieso["ts_utc"] = he_to_ts_utc(ieso["delivery_date"], ieso["hour_ending"], ieso["interval"])
        ieso["available_at_utc"] = ieso_file_available(
            ieso["doc_created_at"], ieso["listing"], avail.listing_margin_minutes
        )
        ieso["source"] = "ieso"
        frames.append(ieso)
    gs, sha = gridstatus_frame(data_dir, "ieso_lmp_real_time_5_min_ontario_zonal")
    if not gs.empty:
        ts = _ts(gs["interval_start_utc"])
        est = ts.dt.tz_convert(None) - pd.Timedelta(hours=5)
        frames.append(
            pd.DataFrame(
                {
                    "ts_utc": ts,
                    "delivery_date": est.dt.date,
                    "hour_ending": (est.dt.hour + 1).astype("int8"),
                    "interval": (est.dt.minute // 5 + 1).astype("int8"),
                    "lmp": gs["lmp"].astype("float64"),
                    "loss_price_capped": gs["loss"].astype("float64"),
                    "congestion_price_capped": gs["congestion"].astype("float64"),
                    "flag": None,
                    "version": pd.NA,
                    "source": "gridstatus",
                    "available_at_utc": after_end_rule(
                        _ts(gs["interval_end_utc"]), avail.rt_ozp_gridstatus
                    ),
                    "source_sha256": sha.to_numpy(),
                }
            )
        )
    return _finish(frames, "rt_ozp_5min")


def build_rt_ozp_hourly(rt5: pd.DataFrame) -> pd.DataFrame:
    if rt5.empty:
        return _finish([], "rt_ozp_hourly")
    hourly = he_to_ts_utc(rt5["delivery_date"], rt5["hour_ending"])
    grouped = rt5.assign(hour_ts=hourly).groupby(["hour_ts", "source"], sort=True)
    out = grouped.agg(
        delivery_date=("delivery_date", "first"),
        hour_ending=("hour_ending", "first"),
        lmp_mean=("lmp", "mean"),
        n_intervals=("lmp", "size"),
        available_at_utc=("available_at_utc", "max"),
    ).reset_index()
    out = out.rename(columns={"hour_ts": "ts_utc"})
    out["n_intervals"] = out["n_intervals"].astype("int8")
    return _finish([out], "rt_ozp_hourly")


def _yearly_csv(
    data_dir: Path,
    report: str,
    parser: Callable[[bytes], pd.DataFrame],
    avail_rule: object,
    name: str,
) -> pd.DataFrame:
    files = _latest_version_per_key(manifest_files(data_dir, report))
    frame = _parse_files(data_dir, files, parser)
    if frame.empty:
        return _finish([], name)
    frame["ts_utc"] = he_to_ts_utc(frame["delivery_date"], frame["hour_ending"])
    frame["available_at_utc"] = est_time_rule(frame["delivery_date"], avail_rule)  # type: ignore[arg-type]
    # Overlapping yearly files (a year's final file plus the current one) keep the newest.
    frame = frame.drop_duplicates("ts_utc", keep="last")
    return _finish([frame], name)


def build_demand(data_dir: Path, avail: AvailabilityConfig) -> pd.DataFrame:
    return _yearly_csv(data_dir, "Demand", parse_demand, avail.demand_ieso, "demand")


def build_hoep(data_dir: Path, avail: AvailabilityConfig) -> pd.DataFrame:
    return _yearly_csv(data_dir, "PriceHOEPPredispOR", parse_hoep, avail.hoep_ieso, "hoep")


def build_weather(data_dir: Path, config: WeatherConfig) -> pd.DataFrame:
    path = data_dir / "bronze" / "_weather.sqlite"
    if not path.exists():
        return _finish([], "weather_fcst")
    with sqlite3.connect(path) as db:
        fetches = pd.read_sql_query(
            "SELECT model, point, fetched_at_utc, path FROM fetches WHERE model=?"
            " ORDER BY fetched_at_utc, id",
            db,
            params=(config.model,),
        )
    latency = pd.Timedelta(hours=config.latency_hours)
    parts = []
    for row in fetches.itertuples(index=False):
        hourly = json.loads(read_bronze(data_dir, str(row.path)))["hourly"]
        valid = pd.to_datetime(pd.Series(hourly["time"]), format="%Y-%m-%dT%H:%M").dt.tz_localize(
            "UTC"
        )
        fetched = pd.Timestamp(str(row.fetched_at_utc)).tz_convert("UTC")
        for lead in config.lead_days:
            part = pd.DataFrame({"valid_ts_utc": valid.astype(TS_DTYPE)})
            for var in WEATHER_VARS:
                part[var] = pd.to_numeric(
                    pd.Series(hourly.get(f"{var}_previous_day{lead}")), errors="coerce"
                )
            part["lead_days"] = lead
            part["available_at_utc"] = (
                part["valid_ts_utc"] - pd.Timedelta(days=lead) + latency
            ).astype(TS_DTYPE)
            part["fetched_at_utc"] = fetched
            part["point"] = row.point
            part["model"] = row.model
            # Open-Meteo fills not-yet-existing previous runs with the latest run (V-10).
            parts.append(part[part["available_at_utc"] <= fetched])
    if not parts:
        return _finish([], "weather_fcst")
    frame = pd.concat(parts, ignore_index=True)
    frame["fetched_at_utc"] = frame["fetched_at_utc"].astype(TS_DTYPE)
    frame["lead_days"] = frame["lead_days"].astype("int8")
    frame = frame.drop_duplicates(["point", "valid_ts_utc", "lead_days"], keep="last")
    return _finish([frame], "weather_fcst")


# ------------------------------------------------------------------ finishing & io


def _finish(frames: list[pd.DataFrame], name: str) -> pd.DataFrame:
    schema = SCHEMAS[name]
    columns = list(schema.columns)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns}).iloc[0:0]
    frame = pd.concat([f.reindex(columns=columns) for f in frames], ignore_index=True)
    for col, spec in schema.columns.items():
        if spec.dtype is not None and str(spec.dtype) != str(frame[col].dtype):
            frame[col] = frame[col].astype(str(spec.dtype))
    frame = frame.sort_values(
        [
            TS_COLUMN[name],
            *[
                c
                for c in ("source", "version", "issued_at_utc", "point", "lead_days")
                if c in frame
            ],
        ]
    )
    frame = frame.reset_index(drop=True)
    return schema.validate(frame)


def write_table(frame: pd.DataFrame, data_dir: Path, name: str) -> int:
    """Replace ``silver/<name>`` atomically with month partitions (by UTC month)."""
    final = data_dir / "silver" / name
    tmp = data_dir / "silver" / f".{name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if not frame.empty:
        month = frame[TS_COLUMN[name]].dt.strftime("%Y-%m")
        for key, part in frame.groupby(month, sort=True):
            out = tmp / f"month={key}"
            out.mkdir()
            part.to_parquet(out / "part-0.parquet", index=False)
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    return len(frame)


def build_all(data_dir: Path) -> dict[str, int]:
    avail = load_availability_config()
    tables: dict[str, pd.DataFrame] = {
        "da_ozp": build_da_ozp(data_dir, avail),
        "predisp_ozp": build_predisp_ozp(data_dir, avail),
        "rt_ozp_5min": build_rt_ozp_5min(data_dir, avail),
        "demand": build_demand(data_dir, avail),
        "hoep": build_hoep(data_dir, avail),
        "weather_fcst": build_weather(data_dir, load_weather_config()),
    }
    tables["rt_ozp_hourly"] = build_rt_ozp_hourly(tables["rt_ozp_5min"])
    counts = {name: write_table(frame, data_dir, name) for name, frame in tables.items()}
    log.info("silver_built", **counts)
    return counts
