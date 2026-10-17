"""Namespace-aware parsers for IESO XML price reports (M1-T4)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from lxml import etree

from gridcast.timeutil.ieso import ieso_naive_to_utc

NS = {"i": "http://www.ieso.ca/schema"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _root(content: bytes) -> etree._Element:
    root = etree.fromstring(content, parser=_PARSER)
    if etree.QName(root).namespace != NS["i"]:
        raise ValueError(f"unexpected XML namespace: {etree.QName(root).namespace}")
    return root


def _text(node: etree._Element, path: str) -> str | None:
    found = node.find(path, namespaces=NS)
    return found.text.strip() if found is not None and found.text else None


def _num(node: etree._Element, path: str) -> float:
    value = _text(node, path)
    return float(value) if value not in (None, "") else float("nan")


def _header(root: etree._Element) -> tuple[date, datetime | None]:
    delivery = _text(root, "i:DocBody/i:DeliveryDate")
    if delivery is None:
        raise ValueError("report has no DeliveryDate")
    created = _text(root, "i:DocHeader/i:CreatedAt")
    created_utc = ieso_naive_to_utc(datetime.fromisoformat(created)) if created else None
    return date.fromisoformat(delivery), created_utc


def _hourly_components(content: bytes, with_flag: bool) -> pd.DataFrame:
    root = _root(content)
    delivery, created = _header(root)
    rows = []
    for node in root.iterfind("i:DocBody/i:HourlyPriceComponents", namespaces=NS):
        row: dict[str, object] = {
            "delivery_date": delivery,
            "hour_ending": int(_text(node, "i:PricingHour") or 0),
            "zonal_price": _num(node, "i:ZonalPrice"),
            "loss_price_capped": _num(node, "i:LossPriceCapped"),
            "congestion_price_capped": _num(node, "i:CongestionPriceCapped"),
        }
        if with_flag:
            row["flag"] = _text(node, "i:Flag")
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame["doc_created_at"] = created
    return frame


def parse_da_ozp(content: bytes) -> pd.DataFrame:
    """``DAHourlyOntarioZonalPrice``: one row per hour-ending."""
    return _hourly_components(content, with_flag=True)


def parse_predisp_ozp(content: bytes) -> pd.DataFrame:
    """``PredispHourlyOntarioZonalPrice``: one vintage, one row per hour-ending."""
    return _hourly_components(content, with_flag=False)


def parse_rt_ozp(content: bytes) -> pd.DataFrame:
    """``RealtimeOntarioZonalPrice``: one delivery hour, one row per 5-minute interval."""
    root = _root(content)
    delivery, created = _header(root)
    hour = int(_text(root, "i:DocBody/i:DeliveryHour") or 0)
    rows = [
        {
            "delivery_date": delivery,
            "hour_ending": hour,
            "interval": int(_text(node, "i:Interval") or 0),
            "lmp": _num(node, "i:LmpCap"),
            "loss_price_capped": _num(node, "i:LossPriceCap"),
            "congestion_price_capped": _num(node, "i:CongPriceCap"),
            "flag": _text(node, "i:Flag"),
        }
        for node in root.iterfind("i:DocBody/i:ZonalPrice", namespaces=NS)
    ]
    frame = pd.DataFrame(rows)
    frame["doc_created_at"] = created
    return frame
