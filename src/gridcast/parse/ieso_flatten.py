"""Flatten nested IESO hourly reports (Adequacy3, VGForecastSummary, DATotals) into series.

Any element with an hour child (``DeliveryHour`` or ``ForecastHour``) is an hourly value
holder. Its series name is the path below ``DocBody``, with a container replaced by its
qualifier (``FuelType``, ``ZoneName``, ``OrganizationType``, ``MarketQuantity``) when it
has one. ``ForecastDate`` qualifiers set the date instead of being part of the name.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import cache

import pandas as pd
from lxml import etree

from gridcast.parse.ieso_xml import NS, _root, _text
from gridcast.timeutil.ieso import ieso_naive_to_utc

_NS = "{" + NS["i"] + "}"
HOUR_TAGS = (f"{_NS}DeliveryHour", f"{_NS}ForecastHour")
QUALIFIERS = frozenset(
    f"{_NS}{t}" for t in ("FuelType", "ZoneName", "OrganizationType", "MarketQuantity")
)
DATE_TAG = f"{_NS}ForecastDate"


@cache
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(node: etree._Element) -> float | None:
    if len(node) or node.text is None:
        return None
    try:
        return float(node.text)
    except ValueError:
        return None


def _qualifier(node: etree._Element) -> str | None:
    for child in node:
        if child.tag in QUALIFIERS and child.text:
            return child.text.strip()
    return None


def flatten_hourly(content: bytes) -> tuple[pd.DataFrame, datetime | None]:
    """Long frame ``delivery_date, hour_ending, series, value`` plus the report's CreatedAt."""
    root = _root(content)
    body = root.find("i:DocBody", namespaces=NS)
    if body is None:
        raise ValueError("report has no DocBody")
    delivery = _text(body, "i:DeliveryDate")
    default_day = date.fromisoformat(delivery) if delivery else None
    created = _text(root, "i:DocHeader/i:CreatedAt")
    created_utc = ieso_naive_to_utc(datetime.fromisoformat(created)) if created else None

    # Path and date of every container, computed once per container element.
    context: dict[etree._Element, tuple[str, date | None]] = {}

    def resolve(container: etree._Element) -> tuple[str, date | None]:
        if container is body:
            return "", default_day
        if container in context:
            return context[container]
        parent = container.getparent()
        prefix, day = resolve(parent) if parent is not None else ("", default_day)
        date_child = next((c for c in container if c.tag == DATE_TAG and c.text), None)
        result: tuple[str, date | None]
        if date_child is not None and date_child.text is not None:
            result = (prefix, date.fromisoformat(date_child.text.strip()))
        else:
            name = _qualifier(container) or _local(str(container.tag))
            result = (f"{prefix}/{name}" if prefix else name, day)
        context[container] = result
        return result

    rows: list[tuple[date | None, int, str, float]] = []
    for hour_node in body.iter(*HOUR_TAGS):
        holder = hour_node.getparent()
        if holder is None or hour_node.text is None:
            continue
        parent = holder.getparent()
        prefix, day = resolve(parent) if parent is not None else ("", default_day)
        hour = int(hour_node.text)
        head = f"{prefix}/{_local(str(holder.tag))}"
        for child in holder:
            if child is hour_node:
                continue
            value = _number(child)
            if value is not None:
                rows.append((day, hour, f"{head}.{_local(str(child.tag))}", value))
            elif len(child) and (name := _qualifier(child)):
                for leaf in child:
                    leaf_value = _number(leaf)
                    if leaf_value is not None:
                        rows.append(
                            (day, hour, f"{head}/{name}.{_local(str(leaf.tag))}", leaf_value)
                        )
    frame = pd.DataFrame(rows, columns=["delivery_date", "hour_ending", "series", "value"])
    return frame, created_utc


def forecast_timestamp(content: bytes) -> datetime | None:
    """``ForecastTimeStamp`` (VGForecastSummary), EST -> UTC."""
    stamp = _text(_root(content), "i:DocBody/i:ForecastTimeStamp")
    return ieso_naive_to_utc(datetime.fromisoformat(stamp)) if stamp else None


def _fuel_series(fuel: str, offered: str) -> dict[str, str]:
    base = f"ForecastSupply/InternalResources/{fuel}"
    key = fuel.lower()
    return {
        f"{key}_capacity": f"{base}/Capacities/Capacity.EnergyMW",
        f"{key}_outages": f"{base}/Outages/Outage.EnergyMW",
        f"{key}_offered": f"{base}/{offered}.EnergyMW",
        f"{key}_scheduled": f"{base}/Schedules/Schedule.EnergyMW",
    }


ADEQUACY_SERIES: dict[str, str] = {
    "ont_demand_fcst": "ForecastDemand/OntarioDemand/ForecastOntDemand/Demand.EnergyMW",
    "ont_peak_demand_fcst": "ForecastDemand/OntarioDemand/PeakDemand/Demand.EnergyMW",
    "wind_embedded_fcst": "ForecastDemand/OntarioDemand/WindEmbedded/Embedded.EnergyMW",
    "solar_embedded_fcst": "ForecastDemand/OntarioDemand/SolarEmbedded/Embedded.EnergyMW",
    "total_requirement": "ForecastDemand/TotalRequirements/Requirement.EnergyMW",
    "excess_capacity": "ForecastDemand/ExcessCapacities/Capacity.EnergyMW",
    "excess_offered_capacity": "ForecastDemand/ExcessOfferedCapacities/Capacity.EnergyMW",
    "or_requirement": "ForecastDemand/GenerationReserveHoldback/TotalORReserve/ORReserve.EnergyMW",
    "exports_bid": "ForecastDemand/ZonalExports/TotalExports/Bids/Bid.EnergyMW",
    "exports_scheduled": "ForecastDemand/ZonalExports/TotalExports/Schedules/Schedule.EnergyMW",
    "imports_offered": "ForecastSupply/ZonalImports/TotalImports/Offers/Offer.EnergyMW",
    "imports_scheduled": "ForecastSupply/ZonalImports/TotalImports/Schedules/Schedule.EnergyMW",
    "supply_capacity": "ForecastSupply/Capacities/Capacity.EnergyMW",
    "total_supply": "ForecastSupply/TotalSupplies/Supply.EnergyMW",
    **_fuel_series("Nuclear", "Offers/Offer"),
    **_fuel_series("Gas", "Offers/Offer"),
    **_fuel_series("Hydro", "Offers/Offer"),
    **_fuel_series("Biofuel", "Offers/Offer"),
    **_fuel_series("Wind", "Forecasts/Forecast"),
    **_fuel_series("Solar", "Forecasts/Forecast"),
    **_fuel_series("Storage", "OfferForecasts/OfferForecast"),
}

VG_SERIES: dict[str, str] = {
    "wind_mp_fcst": "MARKET PARTICIPANT/Wind/OntarioTotal/ForecastInterval.MWOutput",
    "solar_mp_fcst": "MARKET PARTICIPANT/Solar/OntarioTotal/ForecastInterval.MWOutput",
    "wind_embedded_fcst": "EMBEDDED/Wind/OntarioTotal/ForecastInterval.MWOutput",
    "solar_embedded_fcst": "EMBEDDED/Solar/OntarioTotal/ForecastInterval.MWOutput",
}

DA_TOTALS_SERIES: dict[str, str] = {
    "da_total_energy": "Energies/HourlyEnergy/Total Energy.EnergyMW",
    "da_total_load": "Energies/HourlyEnergy/Total Load.EnergyMW",
    "da_total_loss": "Energies/HourlyEnergy/Total Loss.EnergyMW",
    "da_dispatch_load": "Energies/HourlyEnergy/Total Dispatch Load.EnergyMW",
    "da_or_10n": "Energies/HourlyEnergy/Total 10N.EnergyMW",
    "da_or_10s": "Energies/HourlyEnergy/Total 10S.EnergyMW",
    "da_or_30r": "Energies/HourlyEnergy/Total 30R.EnergyMW",
}


def wide_series(content: bytes, mapping: dict[str, str]) -> tuple[pd.DataFrame, datetime | None]:
    """One row per (delivery_date, hour_ending) with the mapped series as columns."""
    long, created = flatten_hourly(content)
    rename = {path: name for name, path in mapping.items()}
    cells: dict[tuple[date | None, int], dict[str, float]] = {}
    for day, hour, series, value in long.itertuples(index=False):
        name = rename.get(series)
        if name is not None:
            cells.setdefault((day, hour), {}).setdefault(name, value)
    rows = [
        {"delivery_date": d, "hour_ending": h, **vals} for (d, h), vals in sorted(cells.items())
    ]
    wide = pd.DataFrame(rows, columns=["delivery_date", "hour_ending", *mapping])
    return wide, created
