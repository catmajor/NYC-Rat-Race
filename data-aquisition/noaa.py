import time
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

START_YEAR = 2000
END_YEAR = 2025

LAT_MIN, LAT_MAX = 40.45, 41.05
LON_MIN, LON_MAX = -74.30, -73.55

OUT = Path("noaa_isd_nyc")
OUT.mkdir(parents=True, exist_ok=True)

STATION_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"

MAX_RETRIES = 5


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    max_retries=3,
)

session.mount("https://", adapter)
session.mount("http://", adapter)


# ============================================================
# LOAD STATIONS
# ============================================================

print("Downloading station metadata...")

stations = pd.read_csv(
    STATION_URL,
    dtype={
        "USAF": str,
        "WBAN": str,
    },
)

stations["LAT"] = pd.to_numeric(
    stations["LAT"],
    errors="coerce",
)

stations["LON"] = pd.to_numeric(
    stations["LON"],
    errors="coerce",
)

nyc = stations[
    stations["LAT"].between(LAT_MIN, LAT_MAX)
    &
    stations["LON"].between(LON_MIN, LON_MAX)
].copy()

nyc["STATION_ID"] = (
    nyc["USAF"].str.zfill(6)
    + nyc["WBAN"].str.zfill(5)
)

print(f"Found {len(nyc)} NYC-area stations")


# ============================================================
# DOWNLOAD FUNCTION
# ============================================================

def download_file(url: str, destination: Path):
    """
    Crash-safe downloader.

    Downloads to .part first.
    Only renames to final filename after successful completion.
    """

    temp_file = destination.with_suffix(
        destination.suffix + ".part"
    )

    # Already successfully downloaded
    if destination.exists():
        return "exists"

    # Remove broken partial download from previous crash
    if temp_file.exists():
        print(f"    removing incomplete file: {temp_file.name}")
        temp_file.unlink()

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            with session.get(
                url,
                stream=True,
                timeout=(15, 120),
            ) as response:

                if response.status_code == 404:
                    return "missing"

                response.raise_for_status()

                with open(temp_file, "wb") as f:

                    for chunk in response.iter_content(
                        chunk_size=1024 * 1024
                    ):
                        if chunk:
                            f.write(chunk)

            # Atomic rename after successful download
            temp_file.replace(destination)

            return "downloaded"

        except (
            requests.RequestException,
            OSError,
        ) as e:

            print(
                f"    attempt {attempt}/{MAX_RETRIES} failed: {e}"
            )

            if temp_file.exists():
                temp_file.unlink()

            if attempt < MAX_RETRIES:
                time.sleep(min(2 ** attempt, 30))

    return "failed"


# ============================================================
# BUILD DOWNLOAD LIST
# ============================================================

jobs = []

for _, station in nyc.iterrows():

    station_id = station["STATION_ID"]

    try:
        station_start = int(str(station["BEGIN"])[:4])
        station_end = int(str(station["END"])[:4])
    except (ValueError, TypeError):
        continue

    first_year = max(
        START_YEAR,
        station_start,
    )

    last_year = min(
        END_YEAR,
        station_end,
    )

    for year in range(first_year, last_year + 1):

        jobs.append(
            (
                station_id,
                station["STATION NAME"],
                year,
            )
        )


# ============================================================
# DOWNLOAD
# ============================================================

print(f"Total possible station/year files: {len(jobs):,}")

completed = 0
skipped = 0
missing = 0
failed = 0

for i, (station_id, station_name, year) in enumerate(
    jobs,
    start=1,
):

    destination = (
        OUT /
        f"{station_id}_{year}.csv"
    )

    url = (
        "https://noaa-global-hourly-pds.s3.amazonaws.com/"
        f"{year}/{station_id}.csv"
    )

    # Immediately skip completed work
    if destination.exists():
        skipped += 1

        print(
            f"[{i}/{len(jobs)}] "
            f"SKIP {year} {station_name}"
        )

        continue

    print(
        f"[{i}/{len(jobs)}] "
        f"{year} {station_name}"
    )

    result = download_file(
        url,
        destination,
    )

    if result == "downloaded":

        completed += 1

        size_mb = destination.stat().st_size / 1024 / 1024

        print(
            f"    ✓ downloaded "
            f"({size_mb:.2f} MB)"
        )

    elif result == "missing":

        missing += 1
        print("    - not available")

    elif result == "failed":

        failed += 1
        print("    ✗ failed after retries")


# ============================================================
# SUMMARY
# ============================================================

print("\n========================================")
print("DONE")
print("========================================")

print(f"New downloads:  {completed:,}")
print(f"Already done:   {skipped:,}")
print(f"Not available:  {missing:,}")
print(f"Failed:         {failed:,}")
