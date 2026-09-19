import time
from pathlib import Path

import requests


# ============================================================
# CONFIG
# ============================================================

START_YEAR = 2015
END_YEAR = 2026

# Current month cutoff for 2026.
# TLC data is published with a delay, so unavailable months
# will simply return 404 and be skipped.
END_MONTH = 12

OUT = Path("tlc_nyc")
OUT.mkdir(parents=True, exist_ok=True)

MAX_RETRIES = 5

# TLC dataset names used by the anonymous CDN.
DATASETS = {
    "yellow": "yellow_tripdata",
    "green": "green_tripdata",
    "fhv": "fhv_tripdata",
    "fhvhv": "fhvhv_tripdata",
}

BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    max_retries=3,
)

session.mount("https://", adapter)


# ============================================================
# DOWNLOAD FUNCTION
# ============================================================

def download_file(url: str, destination: Path):
    """
    Crash-safe download.

    The file is written to *.part first.
    It becomes a real .parquet file only when complete.
    """

    temp = destination.with_suffix(
        destination.suffix + ".part"
    )

    # Already successfully downloaded.
    if destination.exists():
        return "exists"

    # Remove incomplete previous attempt.
    if temp.exists():
        print(f"    removing incomplete {temp.name}")
        temp.unlink()

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            with session.get(
                url,
                stream=True,
                timeout=(15, 180),
            ) as r:

                if r.status_code == 404:
                    return "missing"

                r.raise_for_status()

                expected_size = int(
                    r.headers.get("content-length", 0)
                )

                downloaded = 0

                with open(temp, "wb") as f:

                    for chunk in r.iter_content(
                        chunk_size=1024 * 1024
                    ):
                        if not chunk:
                            continue

                        f.write(chunk)
                        downloaded += len(chunk)

                # Additional corruption/incomplete check.
                if (
                    expected_size
                    and downloaded != expected_size
                ):
                    raise IOError(
                        f"Expected {expected_size} bytes, "
                        f"downloaded {downloaded}"
                    )

            # Atomic finalization.
            temp.replace(destination)

            return "downloaded"

        except (
            requests.RequestException,
            OSError,
        ) as e:

            print(
                f"    attempt {attempt}/{MAX_RETRIES} "
                f"failed: {e}"
            )

            if temp.exists():
                temp.unlink()

            if attempt < MAX_RETRIES:
                time.sleep(
                    min(2 ** attempt, 30)
                )

    return "failed"


# ============================================================
# CREATE JOBS
# ============================================================

jobs = []

for year in range(
    START_YEAR,
    END_YEAR + 1,
):

    max_month = (
        END_MONTH
        if year == END_YEAR
        else 12
    )

    for month in range(
        1,
        max_month + 1,
    ):

        for dataset_name, prefix in DATASETS.items():

            # HVFHV starts in 2019.
            if dataset_name == "fhvhv" and year < 2019:
                continue

            filename = (
                f"{prefix}_{year}-{month:02d}.parquet"
            )

            jobs.append(
                (
                    dataset_name,
                    year,
                    month,
                    filename,
                )
            )


print(f"Potential files: {len(jobs):,}")


# ============================================================
# DOWNLOAD
# ============================================================

downloaded_count = 0
skipped_count = 0
missing_count = 0
failed_count = 0


for i, (
    dataset,
    year,
    month,
    filename,
) in enumerate(
    jobs,
    start=1,
):

    dataset_dir = OUT / dataset
    dataset_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = (
        dataset_dir /
        filename
    )

    url = (
        f"{BASE_URL}/"
        f"{filename}"
    )

    print(
        f"[{i}/{len(jobs)}] "
        f"{dataset.upper()} "
        f"{year}-{month:02d}"
    )

    if destination.exists():

        skipped_count += 1

        print("    already complete")

        continue

    result = download_file(
        url,
        destination,
    )

    if result == "downloaded":

        downloaded_count += 1

        size_mb = (
            destination.stat().st_size
            / 1024
            / 1024
        )

        print(
            f"    ✓ downloaded "
            f"{size_mb:.1f} MB"
        )

    elif result == "missing":

        missing_count += 1

        print(
            "    - not available"
        )

    else:

        failed_count += 1

        print(
            "    ✗ failed"
        )


# ============================================================
# SUMMARY
# ============================================================

print("\n========================================")
print("DONE")
print("========================================")

print(f"Downloaded: {downloaded_count:,}")
print(f"Already done: {skipped_count:,}")
print(f"Unavailable: {missing_count:,}")
print(f"Failed:      {failed_count:,}")
print(f"Output:      {OUT.resolve()}")
