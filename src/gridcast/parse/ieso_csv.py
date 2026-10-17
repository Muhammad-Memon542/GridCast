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
