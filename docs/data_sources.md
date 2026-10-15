# Data sources

One row per dataset in plan §6. Earliest-available dates and the ADR-001 recommendation come from the M1-T1 audit (`docs/data_availability.md`). Format facts below were checked on 2026-10-15 (`docs/decisions.md`, V-05).

**Time conventions (V-02, V-03):** IESO hours are hour-ending 1–24 on **EST (UTC−5) all year**. `CreatedAt` and directory-listing times are EST too. In silver tables, `ts_utc` is the interval start in UTC.

| ID | Dataset | Source / URL | Format | On-site retention | Licence / terms | Status |
| --- | --- | --- | --- | --- | --- | --- |
| D01 | Day-ahead OZP | `reports-public.ieso.ca/public/DAHourlyOntarioZonalPrice/` | XML, 1 file per delivery date, `_vN` versions; the base file is an alias of the latest | ~90 days | IESO terms (to check in M1-T1) | Format verified |
| D02 | Real-time OZP | `…/RealtimeOntarioZonalPrice/` | XML, **1 file per delivery hour** (`YYYYMMDDHH`), 12 five-minute intervals, `_vN` grows per interval | ~90 days | IESO | Format verified |
| D03 | Pre-dispatch OZP | `…/PredispHourlyOntarioZonalPrice/` | XML, 1 file per delivery date, ~27 hourly vintages from ~20:19 EST D−1 | ~30 days of all vintages, then only base + final to ~90 days | IESO | Format verified |
| D04 | HOEP | `…/PriceHOEPPredispOR/` | CSV | 2002 → May 2025 | IESO | M1-T1 |
| D05 | Demand | `…/Demand/` | Yearly CSV with 3 `\\` header lines, `Date,Hour,Market Demand,Ontario Demand` | 2002 → present | IESO | Format verified |
| D06 | Adequacy | `…/Adequacy3/` | XML | ~90 days | IESO | M1-T1 |
| D07 | Generator output & capability | `…/GenOutputCapability/`, `…/GenOutputCapabilityMonth/` | XML / monthly CSV | Monthly CSV from May 2019 | IESO | M1-T1 |
| D08 | Wind/solar forecast | `…/VGForecastSummary/` | XML | ~30 days | IESO | M1-T1 |
| D09 | Intertie flows | `…/IntertieScheduleFlow/`, `…/IntertieScheduleFlowYear/` | XML / yearly CSV | Yearly CSV from 2018 | IESO | M1-T1 |
| D10 | Virtual zonal prices | `…/DAHourlyZonal/`, `…/RealtimeZonalEnergyPrices/` | XML | ~90 days | IESO | M1-T1 |
| D11 | DA shadow prices | `…/DAConstrShadowPrices/` | XML | ~90 days | IESO | M1-T1 |
| D12 | Weather forecasts | Open-Meteo Previous Runs API | JSON | Most models from Jan 2024 | Free for non-commercial use | ADR-003 |
| D13 | Weather actuals | Open-Meteo ERA5 archive | JSON | 1940 → | Free for non-commercial use | Analysis only |
| D14 | Holidays | `holidays` Python package (Ontario) | Python | n/a | MIT | M1 |
| D15 | Gas price | `[OWNER]` picks the source | — | — | — | ADR-006 |
| — | Post-May-2025 price backfill | ADR-001: Grid Status API / IESO Customer Relations / other | — | — | Hosted-API data likely **not redistributable** | ADR-001 |

## Collection etiquette (plan §0 rule 10)

- User-Agent `GridCast/<version> (non-commercial research; +<contact>)`. The contact comes from `GRIDCAST_CONTACT` `[OWNER]`.
- At most 1 request per second per host (`GRIDCAST_HTTP_MIN_INTERVAL_S` ≥ 1.0 is enforced in config).
- Exponential backoff with retries. The IESO server occasionally resets connections (V-05h).
- Raw files are cached in bronze and keyed by sha256. Unchanged files are never re-downloaded.
