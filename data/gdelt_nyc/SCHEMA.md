# GDELT NYC Events - Schema Reference

Profiled from the monthly Parquet files in the supplied NYC-filtered GDELT
event dataset. The authoritative field definitions are in the
[GDELT Event Codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf).

## Overview

| Dataset | Files | Total rows | Logical size | Coverage |
|---------|------:|-----------:|-------------:|----------|
| NYC-filtered GDELT events | 140 Parquet files | 2,776,107 | 102.6 MB | 2015-02 .. 2026-09 |

**File naming:** `gdelt_events_nyc_<YYYY>_<MM>.parquet`.

**Partitioning:** every file is one calendar month of `SQLDATE`. All 140 files
have the same 25-column schema and one Parquet row group.

**Record meaning:** one row is one GDELT event extraction from news coverage.
It is not one article and not one event mention. `NumMentions`, `NumSources`,
and `NumArticles` summarize coverage for the event when it was first seen.

## Current schema

All columns are nullable in the Parquet schema. String columns are stored as
Arrow `large_string`; numeric columns are `int64` or `double`.

### Identity and time

| Column | Type | Description |
|--------|------|-------------|
| `GLOBALEVENTID` | int64 | GDELT event identifier; intended to identify the event record globally |
| `SQLDATE` | int64 | Event date in `YYYYMMDD` format |
| `DATEADDED` | int64 | UTC timestamp when GDELT added the event, in `YYYYMMDDHHMMSS` format |

`SQLDATE` is daily-granularity event time. `DATEADDED` is the ingestion/update
time and is the field to use when 15-minute timing matters.

### Actors

| Column | Type | Description |
|--------|------|-------------|
| `Actor1Code` | large_string | Complete raw CAMEO code for actor 1 |
| `Actor1Name` | large_string | Human-readable actor 1 name |
| `Actor1CountryCode` | large_string | Three-character CAMEO country affiliation for actor 1 |
| `Actor2Code` | large_string | Complete raw CAMEO code for actor 2 |
| `Actor2Name` | large_string | Human-readable actor 2 name |
| `Actor2CountryCode` | large_string | Three-character CAMEO country affiliation for actor 2 |

Actor fields can be null for single-actor events, unidentified actors, or
events where an actor attribute could not be inferred. Actor country codes
describe actor affiliation and should not be confused with event geography.

### Event classification and intensity

| Column | Type | Description |
|--------|------|-------------|
| `IsRootEvent` | int64 | `1` when the event is treated as a root/lead event; otherwise `0` |
| `EventCode` | large_string | Detailed CAMEO action code |
| `EventBaseCode` | large_string | Intermediate CAMEO aggregation code |
| `EventRootCode` | large_string | Root-level CAMEO aggregation code |
| `QuadClass` | int64 | Broad CAMEO class: 1 verbal cooperation, 2 material cooperation, 3 verbal conflict, 4 material conflict |
| `GoldsteinScale` | double | Theoretical event-type impact score, from -10 to +10 |
| `NumMentions` | int64 | Number of mentions of the event in the first 15-minute update |
| `NumSources` | int64 | Number of information sources containing the event in that update |
| `NumArticles` | int64 | Number of source documents containing the event in that update |
| `AvgTone` | double | Average tone of documents containing the event; negative is more negative, positive is more positive |

Event codes must remain strings so leading zeroes are preserved. The local
dataset contains 239 distinct `EventCode` values, 142 distinct
`EventBaseCode` values, and 21 distinct `EventRootCode` values.

### Event geography

| Column | Type | Description |
|--------|------|-------------|
| `ActionGeo_FullName` | large_string | Human-readable action location |
| `ActionGeo_CountryCode` | large_string | Two-character FIPS geography code |
| `ActionGeo_ADM1Code` | large_string | First-level administrative geography code |
| `ActionGeo_Lat` | double | Latitude of the action-location centroid |
| `ActionGeo_Long` | double | Longitude of the action-location centroid |

The local data contains only `US` action-country records. Administrative
codes are `USNY`, `USNJ`, and `USCT`:

| Action geography | Rows | Share |
|------------------|-----:|------:|
| New York (`USNY`) | 2,310,799 | 83.24% |
| New Jersey (`USNJ`) | 431,659 | 15.55% |
| Connecticut (`USCT`) | 33,649 | 1.21% |

This local schema does not include GDELT's broader actor-geography fields or
`ActionGeo_FeatureID`. Therefore, locations should be treated as approximate
centroids, and `ActionGeo_FullName` is not a stable geographic key.

### Source

| Column | Type | Description |
|--------|------|-------------|
| `SOURCEURL` | large_string | URL or citation of the first news report in which GDELT found the event |

## Coverage and distributions

The observed event date range is `2015-02-01 .. 2026-09-19`. The observed
`DATEADDED` range is `2015-02-18 22:00:00 .. 2026-09-19 19:00:00` UTC.

| Metric | Minimum | Maximum | Mean |
|--------|--------:|--------:|-----:|
| `GoldsteinScale` | -10.0 | 10.0 | 0.28 |
| `AvgTone` | -29.41 | 20.21 | -2.47 |
| `NumMentions` | 1 | 2,038 | 4.08 |
| `NumSources` | 1 | 60 | 1.07 |
| `NumArticles` | 1 | 317 | 3.98 |

`QuadClass` distribution:

| Value | Meaning | Rows | Share |
|------:|---------|-----:|------:|
| 1 | Verbal cooperation | 1,723,141 | 62.07% |
| 2 | Material cooperation | 276,471 | 9.96% |
| 3 | Verbal conflict | 347,201 | 12.51% |
| 4 | Material conflict | 429,294 | 15.46% |

## Data quality observations

- **Duplicate IDs:** there are 2,776,103 unique `GLOBALEVENTID` values for
  2,776,107 rows; four IDs occur twice. Deduplicate or retain a source-row
  identifier if strict event uniqueness is required.
- **Actor nulls:** `Actor1Code`/`Actor1Name` are null in 13.5% of rows;
  `Actor1CountryCode` in 50.6%; `Actor2Code`/`Actor2Name` in 36.8%; and
  `Actor2CountryCode` in 64.7%.
- **Other missingness:** `GoldsteinScale` is null in 17 rows and `SOURCEURL`
  in 2 rows. The other local columns are fully populated at the storage level.
- **Event time versus ingestion time:** `DATEADDED` can be later than
  `SQLDATE`; 68,008 rows have a positive delay, with a maximum observed delay
  of approximately 10 years. Use `SQLDATE` for event-date analyses and
  `DATEADDED` only when point-in-time availability is the intended concept.
- **News-derived signal:** event counts, tone, source counts, and article counts
  measure news-system visibility and extraction output, not direct physical
  measurements of activity.
- **Geographic scope:** the NYC filter includes nearby New Jersey and
  Connecticut locations. Do not assume every row is inside NYC municipal
  boundaries.

## Suggested load pattern

```python
import glob
import pyarrow.parquet as pq

paths = sorted(glob.glob("gdelt_nyc/events/gdelt_events_nyc_*.parquet"))

for path in paths:
    table = pq.read_table(path)
    # Keep EventCode/EventBaseCode/EventRootCode as strings and parse
    # SQLDATE/DATEADDED explicitly rather than relying on integer inference.
    ...
```

For the game pipeline, aggregate GDELT by `SQLDATE`, action geography, event
root/base code, `QuadClass`, `GoldsteinScale`, `AvgTone`, and coverage counts.
If the feature is meant to be available during a simulated day, filter on
`DATEADDED` first to enforce the point-in-time rule.

## Relationship to NOAA weather data

There is no direct foreign key between GDELT and NOAA. A practical join is:

1. Use GDELT `SQLDATE` as the event date.
2. Aggregate NOAA `DATE` observations to the desired daily or hourly window.
3. Match each event to the nearest NOAA station using `ActionGeo_Lat` and
   `ActionGeo_Long`.

This is an approximate spatial-temporal join, not a relational join. The
GDELT table has action coordinates but no NOAA station identifier.
