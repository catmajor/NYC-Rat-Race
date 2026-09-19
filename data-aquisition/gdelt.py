import time
from pathlib import Path
from datetime import date

from google.cloud import bigquery
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

START_YEAR = 2015
START_MONTH = 2

END_YEAR = 2026
END_MONTH = 9

LAT_MIN, LAT_MAX = 40.45, 41.05
LON_MIN, LON_MAX = -74.30, -73.55

PROJECT_ID = "nyc-rat-race"

OUT = Path("gdelt_nyc")
EVENTS_OUT = OUT / "events"

EVENTS_OUT.mkdir(
    parents=True,
    exist_ok=True,
)

MAX_RETRIES = 5


# ============================================================
# BIGQUERY
# ============================================================

client = bigquery.Client(
    project=PROJECT_ID
)


# ============================================================
# DATE HELPERS
# ============================================================

def next_month(year, month):

    if month == 12:
        return year + 1, 1

    return year, month + 1


def month_ranges(
    start_year,
    start_month,
    end_year,
    end_month,
):

    year = start_year
    month = start_month

    while (year, month) <= (
        end_year,
        end_month,
    ):

        ny, nm = next_month(
            year,
            month,
        )

        start = date(
            year,
            month,
            1,
        )

        end = date(
            ny,
            nm,
            1,
        )

        yield (
            year,
            month,
            start.isoformat(),
            end.isoformat(),
        )

        year, month = ny, nm


# ============================================================
# QUERY ONE MONTH
# ============================================================

def download_month(
    year,
    month,
    start_date,
    end_date,
):

    output = (
        EVENTS_OUT /
        f"gdelt_events_nyc_{year}_{month:02d}.parquet"
    )

    temp = output.with_suffix(
        ".parquet.part"
    )

    # -------------------------------
    # Already completed
    # -------------------------------

    if output.exists():

        print(
            f"SKIP {year}-{month:02d}: "
            f"already downloaded"
        )

        return True

    # -------------------------------
    # Remove incomplete prior attempt
    # -------------------------------

    if temp.exists():

        print(
            f"Removing incomplete "
            f"{temp.name}"
        )

        temp.unlink()

    sql = f"""
    SELECT
        GLOBALEVENTID,
        SQLDATE,

        Actor1Code,
        Actor1Name,
        Actor1CountryCode,

        Actor2Code,
        Actor2Name,
        Actor2CountryCode,

        IsRootEvent,

        EventCode,
        EventBaseCode,
        EventRootCode,

        QuadClass,
        GoldsteinScale,

        NumMentions,
        NumSources,
        NumArticles,
        AvgTone,

        ActionGeo_FullName,
        ActionGeo_CountryCode,
        ActionGeo_ADM1Code,
        ActionGeo_Lat,
        ActionGeo_Long,

        DATEADDED,
        SOURCEURL

    FROM `gdelt-bq.gdeltv2.events_partitioned`

    WHERE
        _PARTITIONTIME >= TIMESTAMP("{start_date}")
        AND _PARTITIONTIME < TIMESTAMP("{end_date}")

        AND ActionGeo_Lat BETWEEN
            {LAT_MIN} AND {LAT_MAX}

        AND ActionGeo_Long BETWEEN
            {LON_MIN} AND {LON_MAX}
    """

    # -------------------------------
    # Retry
    # -------------------------------

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            print(
                f"Querying "
                f"{year}-{month:02d}..."
            )

            query_job = client.query(sql)

            df = (
                query_job
                .result()
                .to_dataframe(
                    create_bqstorage_client=True
                )
            )

            print(
                f"    {len(df):,} NYC events"
            )

            # Save to temporary file first
            df.to_parquet(
                temp,
                index=False,
                compression="zstd",
            )

            # Only mark complete once write succeeds
            temp.replace(output)

            size_mb = (
                output.stat().st_size
                / 1024
                / 1024
            )

            print(
                f"    ✓ saved "
                f"{output.name} "
                f"({size_mb:.2f} MB)"
            )

            return True

        except Exception as e:

            print(
                f"    attempt "
                f"{attempt}/{MAX_RETRIES} "
                f"failed:"
            )

            print(
                f"    {type(e).__name__}: {e}"
            )

            if temp.exists():
                temp.unlink()

            if attempt < MAX_RETRIES:

                delay = min(
                    2 ** attempt,
                    60,
                )

                print(
                    f"    retrying in "
                    f"{delay}s..."
                )

                time.sleep(delay)

    print(
        f"    ✗ gave up on "
        f"{year}-{month:02d}"
    )

    return False


# ============================================================
# MAIN
# ============================================================

months = list(
    month_ranges(
        START_YEAR,
        START_MONTH,
        END_YEAR,
        END_MONTH,
    )
)

print(
    f"Months to process: {len(months)}"
)

successes = 0
failures = 0
skipped = 0

for i, (
    year,
    month,
    start_date,
    end_date,
) in enumerate(
    months,
    start=1,
):

    output = (
        EVENTS_OUT /
        f"gdelt_events_nyc_{year}_{month:02d}.parquet"
    )

    print(
        f"\n[{i}/{len(months)}] "
        f"{year}-{month:02d}"
    )

    if output.exists():

        skipped += 1

        print(
            "    already complete"
        )

        continue

    success = download_month(
        year,
        month,
        start_date,
        end_date,
    )

    if success:
        successes += 1
    else:
        failures += 1


print("\n========================================")
print("DONE")
print("========================================")

print(f"Downloaded: {successes}")
print(f"Skipped:    {skipped}")
print(f"Failed:     {failures}")
print(f"Output:     {EVENTS_OUT.resolve()}")
