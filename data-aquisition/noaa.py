import pandas as pd
import requests
from io import StringIO
from pathlib import Path

START_YEAR = 2000
END_YEAR = 2025

# NYC metro bounding box
LAT_MIN, LAT_MAX = 40.45, 41.05
LON_MIN, LON_MAX = -74.30, -73.55

OUT = Path("noaa_isd_nyc")
OUT.mkdir(exist_ok=True)

# NOAA ISD station list
STATION_URL = (
    "https://www.ncei.noaa.gov/pub/data/noaa/"
    "isd-history.csv"
)

print("Downloading station metadata...")

response = requests.get(STATION_URL, timeout=60)
response.raise_for_status()

stations = pd.read_csv(
    StringIO(response.text),
    dtype={"USAF": str, "WBAN": str},
)

# Convert coordinates to numbers
stations["LAT"] = pd.to_numeric(stations["LAT"], errors="coerce")
stations["LON"] = pd.to_numeric(stations["LON"], errors="coerce")

# Find stations geographically inside the NYC metro area
nyc = stations[
    stations["LAT"].between(LAT_MIN, LAT_MAX)
    & stations["LON"].between(LON_MIN, LON_MAX)
].copy()

# NOAA ISD station ID
nyc["STATION_ID"] = (
    nyc["USAF"].str.zfill(6)
    + nyc["WBAN"].str.zfill(5)
)

print("\nNYC-area stations:")
print(
    nyc[
        [
            "STATION_ID",
            "STATION NAME",
            "STATE",
            "LAT",
            "LON",
            "BEGIN",
            "END",
        ]
    ].sort_values("STATION NAME").to_string(index=False)
)

print(f"\nFound {len(nyc)} stations")

# --------------------------------------------------
# Download only the NYC station/year files
# --------------------------------------------------

for _, station in nyc.iterrows():

    station_id = station["STATION_ID"]

    try:
        station_start = int(str(station["BEGIN"])[:4])
        station_end = int(str(station["END"])[:4])
    except (ValueError, TypeError):
        continue

    first_year = max(START_YEAR, station_start)
    last_year = min(END_YEAR, station_end)

    for year in range(first_year, last_year + 1):

        url = (
            "https://noaa-global-hourly-pds.s3.amazonaws.com/"
            f"{year}/{station_id}.csv"
        )

        destination = OUT / f"{station_id}_{year}.csv"

        # Makes the script resumable
        if destination.exists():
            continue

        try:
            r = requests.get(url, timeout=60)

            if r.status_code == 200:
                destination.write_bytes(r.content)

                size_mb = len(r.content) / 1024 / 1024

                print(
                    f"✓ {year} "
                    f"{station['STATION NAME']} "
                    f"({size_mb:.2f} MB)"
                )

            elif r.status_code == 404:
                # Some station/year combinations aren't present
                pass

            else:
                print(
                    f"ERROR {r.status_code}: {url}"
                )

        except requests.RequestException as e:
            print(f"ERROR downloading {url}: {e}")
