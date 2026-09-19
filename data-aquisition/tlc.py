import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

START_YEAR = 2015
END_YEAR = 2026

# Current TLC data is published with a delay.
# Missing future/unpublished months will just be skipped.
END_MONTH = 12

# Number of simultaneous downloads.
# Start with 6-8. Increase cautiously if your network is fast.
MAX_WORKERS = 8

MAX_RETRIES = 5

OUT = Path("tlc_nyc")
OUT.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"

DATASETS = {
    "yellow": "yellow_tripdata",
    "green": "green_tripdata",
    "fhv": "fhv_tripdata",
    "fhvhv": "fhvhv_tripdata",
}


# ============================================================
# BUILD JOB LIST
# ============================================================

def build_jobs():
    jobs = []

    for year in range(START_YEAR, END_YEAR + 1):

        max_month = END_MONTH if year == END_YEAR else 12

        for month in range(1, max_month + 1):

            for dataset, prefix in DATASETS.items():

                # HVFHV begins in 2019.
                if dataset == "fhvhv" and year < 2019:
                    continue

                filename = f"{prefix}_{year}-{month:02d}.parquet"

                destination = OUT / dataset / filename

                url = f"{BASE_URL}/{filename}"

                jobs.append({
                    "dataset": dataset,
                    "year": year,
                    "month": month,
                    "filename": filename,
                    "url": url,
                    "destination": destination,
                })

    return jobs


# ============================================================
# DOWNLOAD ONE FILE
# ============================================================

def download_one(job):

    destination = job["destination"]

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = destination.with_suffix(
        destination.suffix + ".part"
    )

    # Already completed.
    if destination.exists() and destination.stat().st_size > 0:

        return {
            **job,
            "status": "skipped",
            "size": destination.stat().st_size,
        }

    # Remove old incomplete download.
    if temp_file.exists():
        try:
            temp_file.unlink()
        except OSError:
            pass

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            # Each thread gets its own request rather than
            # sharing one streaming response.
            with requests.get(
                job["url"],
                stream=True,
                timeout=(20, 300),
                headers={
                    "User-Agent": "NYC-Rat-Race/1.0"
                },
            ) as response:

                if response.status_code == 404:

                    return {
                        **job,
                        "status": "missing",
                        "size": 0,
                    }

                response.raise_for_status()

                expected_size = int(
                    response.headers.get(
                        "content-length",
                        0,
                    )
                )

                downloaded = 0

                with open(temp_file, "wb") as f:

                    for chunk in response.iter_content(
                        chunk_size=4 * 1024 * 1024
                    ):

                        if not chunk:
                            continue

                        f.write(chunk)
                        downloaded += len(chunk)

                # Check for truncated downloads.
                if (
                    expected_size > 0
                    and downloaded != expected_size
                ):
                    raise IOError(
                        f"Expected {expected_size:,} bytes, "
                        f"got {downloaded:,}"
                    )

            # Only now is the file considered complete.
            temp_file.replace(destination)

            return {
                **job,
                "status": "downloaded",
                "size": destination.stat().st_size,
            }

        except Exception as exc:

            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

            if attempt == MAX_RETRIES:

                return {
                    **job,
                    "status": "failed",
                    "size": 0,
                    "error": str(exc),
                }

            time.sleep(
                min(2 ** attempt, 30)
            )


# ============================================================
# MAIN
# ============================================================

jobs = build_jobs()

print("========================================")
print("NYC TLC BULK DOWNLOADER")
print("========================================")
print(f"Potential files: {len(jobs):,}")
print(f"Concurrent downloads: {MAX_WORKERS}")
print(f"Output: {OUT.resolve()}")
print()


downloaded = 0
skipped = 0
missing = 0
failed = 0

bytes_downloaded = 0


with ThreadPoolExecutor(
    max_workers=MAX_WORKERS
) as executor:

    futures = {
        executor.submit(
            download_one,
            job,
        ): job

        for job in jobs
    }

    total = len(futures)

    for completed_count, future in enumerate(
        as_completed(futures),
        start=1,
    ):

        result = future.result()

        dataset = result["dataset"].upper()

        ym = (
            f'{result["year"]}-'
            f'{result["month"]:02d}'
        )

        status = result["status"]

        if status == "downloaded":

            downloaded += 1
            bytes_downloaded += result["size"]

            mb = result["size"] / 1024 / 1024

            print(
                f"[{completed_count}/{total}] "
                f"✓ {dataset:6} {ym} "
                f"{mb:8.1f} MB"
            )

        elif status == "skipped":

            skipped += 1

            mb = result["size"] / 1024 / 1024

            print(
                f"[{completed_count}/{total}] "
                f"SKIP {dataset:6} {ym} "
                f"{mb:8.1f} MB"
            )

        elif status == "missing":

            missing += 1

            print(
                f"[{completed_count}/{total}] "
                f"- {dataset:6} {ym} "
                f"not available"
            )

        elif status == "failed":

            failed += 1

            print(
                f"[{completed_count}/{total}] "
                f"✗ {dataset:6} {ym}"
            )

            print(
                f"    {result.get('error')}"
            )


# ============================================================
# SUMMARY
# ============================================================

gb = bytes_downloaded / 1024 / 1024 / 1024

print()
print("========================================")
print("FINISHED")
print("========================================")

print(f"Downloaded:       {downloaded:,}")
print(f"Already complete: {skipped:,}")
print(f"Unavailable:      {missing:,}")
print(f"Failed:           {failed:,}")
print(f"New data:         {gb:.2f} GB")
