# NOAA Integrated Surface Data - Schema Reference

Profiled from the NYC-area station/year CSV files in the supplied dataset.
The source files are NOAA Integrated Surface Data (ISD), also called Global
Hourly data. See the [NOAA ISD format documentation](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf)
for the authoritative field definitions and code lists.

## Overview

| Dataset | Files | Total rows | Logical size | Coverage |
|---------|------:|-----------:|-------------:|----------|
| NYC-area ISD | 263 CSVs | 3,070,688 | 1.43 GB | 17 stations, 2000-01-01 .. 2025-08-27 |

**File naming:** `<station_id>_<YYYY>.csv`, for example
`72503014732_2024.csv`.

**Geographic extent:** latitude `40.4532 .. 41.0495`, longitude
`-74.2996 .. -73.5501`. The stations cover New York City and nearby New
Jersey and Connecticut locations, not only the five NYC boroughs.

**Record meaning:** one row is one reported surface observation. The source
contains sub-hourly aviation reports as well as synoptic and summary reports,
so the rows are not a uniform hourly time series.

## Schema architecture

The CSVs have a variable-width schema. All files contain the 17 base fields
below, but optional additional-data fields are included only when they occur
in that file.

Across the dataset there are 128 possible column names and 162 distinct header
layouts. Individual files contain between 17 and 102 columns. Files must be
unioned by column name before concatenation; positional concatenation is not
safe.

### Base fields

| Column | Logical type | Description |
|--------|--------------|-------------|
| `STATION` | string | 11-character station identifier, normally the USAF/WBAN combination |
| `DATE` | UTC timestamp | Observation timestamp, stored as ISO text such as `2024-01-01T00:56:00` |
| `SOURCE` | categorical string | Source-data code |
| `LATITUDE` | decimal | Station latitude in degrees |
| `LONGITUDE` | decimal | Station longitude in degrees; west longitudes are negative |
| `ELEVATION` | decimal | Station elevation in meters above mean sea level |
| `NAME` | string | Station name |
| `REPORT_TYPE` | categorical string | Report type such as `FM-15`, `FM-13`, `FM-16`, `FM-18`, `NSRDB`, or `SOD` |
| `CALL_SIGN` | string | Station call sign; may contain padded spaces or `99999` when unavailable |
| `QUALITY_CONTROL` | categorical string | NOAA quality-control process code, for example `V020` |
| `WND` | packed string | Wind direction, wind type, wind speed, and quality flags |
| `CIG` | packed string | Ceiling height and quality flags |
| `VIS` | packed string | Visibility and quality flags |
| `TMP` | packed string | Air temperature and quality flag |
| `DEW` | packed string | Dew-point temperature and quality flag |
| `SLP` | packed string | Sea-level pressure and quality flag |
| `REM` | text | Variable-length remarks, often METAR/SPECI text |

The CSV storage type for the packed fields is string. They are not scalar
measurements until their comma-separated components are parsed.

### Packed mandatory measurements

| Field | Component structure | Units / interpretation |
|-------|---------------------|------------------------|
| `WND` | direction, direction QC, type, speed, speed QC | Direction in degrees; speed in tenths of m/s |
| `CIG` | ceiling height, height QC, ceiling determination, CAVOK flag | Height in meters |
| `VIS` | visibility, visibility QC, variability, variability QC | Visibility in meters |
| `TMP` | temperature, temperature QC | Temperature in tenths of degrees Celsius |
| `DEW` | dew point, dew point QC | Dew point in tenths of degrees Celsius |
| `SLP` | pressure, pressure QC | Sea-level pressure in tenths of hPa |

NOAA encodes missing values inside these strings, typically with values such
as `999`, `9999`, `99999`, or `+9999`. A non-empty CSV cell therefore does not
necessarily contain a valid measurement.

### Optional additional-data fields

The observed optional union is:

`AA1-AA4`, `AB1`, `AC1`, `AD1`, `AE1`, `AG1`, `AH1-AH6`, `AI1-AI6`, `AJ1`,
`AK1`, `AL1`, `AM1`, `AN1`, `AT1-AT8`, `AU1-AU5`, `AW1-AW6`, `AX1-AX5`,
`AY1-AY2`, `ED1`, `EQD`, `GA1-GA6`, `GD1-GD4`, `GE1`, `GF1`, `GJ1`, `GK1`,
`GP1`, `GQ1`, `GR1`, `IA1`, `KA1-KA4`, `KB1-KB3`, `KC1-KC2`, `KD1-KD2`,
`KE1`, `KG1-KG2`, `MA1`, `MD1`, `ME1`, `MF1`, `MG1`, `MH1`, `MK1`,
`MV1`, `MW1-MW5`, `OC1`, `OD1`, `OE1-OE3`, `RH1-RH3`, `SA1`, `UA1`, `UG1`,
and `WA1`.

These fields are also packed strings. Common examples include:

- `AA1-AA4`: repeating precipitation groups containing duration, depth,
  condition, and quality values.
- `AW1-AW4`: automated present-weather codes and quality values.
- `GD1-GD6`: repeating sky-cover summation layers.
- `EQD`: element-quality data.

The complete meaning of less common prefixes is defined in the NOAA format
document. Prefixes and suffixes should be preserved as separate fields when
normalizing the data.

## Coverage and distributions

The 17 station IDs are:

`72055399999`, `72058100178`, `72058199999`, `72409454743`, `72502014734`,
`72502594741`, `72502599999`, `72503014732`, `72505394728`, `72506094728`,
`74486094789`, `99727199999`, `99727299999`, `99728099999`, `99728999999`,
`99774399999`, and `99999900178`.

The most frequent report types are:

| Report type | Rows | Share |
|-------------|-----:|------:|
| `FM-15` | 1,682,157 | 54.78% |
| `FM-13` | 444,016 | 14.46% |
| `FM-16` | 263,126 | 8.57% |
| `FM-18` | 225,792 | 7.35% |
| `FM-12` | 153,489 | 5.00% |
| `AUTO` | 112,396 | 3.66% |
| `NSRDB` | 111,183 | 3.62% |

After interpreting missing-value sentinels, the major mandatory measurements
have these valid-value rates:

| Measurement | Valid rows |
|-------------|-----------:|
| Air temperature | 91.4% |
| Wind speed | 90.9% |
| Dew point | 72.2% |
| Visibility | 72.1% |
| Sea-level pressure | 69.5% |
| Wind direction | 69.5% |
| Ceiling height | 68.8% |

## Data quality observations

- **Schema drift:** optional columns vary substantially by station, year, and
  source report type. Build a union schema before loading.
- **Sentinel missingness:** blank-cell counts understate missingness because
  missing values are commonly encoded inside packed strings.
- **Timestamp duplicates:** `STATION + DATE` is not unique. There are 3,089
  rows beyond the first occurrence of a timestamp, usually because different
  report types share the same timestamp. A practical observation key is
  `STATION + DATE + REPORT_TYPE + SOURCE + CALL_SIGN`, subject to validation
  for the specific use case.
- **Station metadata changes:** some station IDs have multiple historical names,
  coordinates, elevations, or call signs. Join station metadata by station and
  observation date when historical precision matters.
- **Temporal granularity:** the data mixes METAR/SPECI-style observations,
  synoptic reports, and summary records. Aggregate by timestamp and report
  type rather than assuming one observation per hour.

## Suggested load pattern

```python
import csv
import glob

paths = sorted(glob.glob("noaa_isd_nyc/*.csv"))

for path in paths:
    # Read by header name because each file can have a different optional schema.
    with open(path, newline="") as handle:
        rows = csv.DictReader(handle)
        for row in rows:
            # Parse DATE, then decode WND/CIG/VIS/TMP/DEW/SLP and sentinel values.
            ...
```

For the game pipeline, retain the raw packed fields for auditability and add
decoded columns such as `temperature_c`, `dewpoint_c`, `wind_speed_mps`,
`wind_direction_deg`, `visibility_m`, `ceiling_m`, `sea_level_pressure_hpa`,
and precipitation totals.
