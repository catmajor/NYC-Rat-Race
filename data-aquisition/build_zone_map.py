"""Build the Rat Race nine-zone map from the official TLC lookup table.

The TLC files expose numeric LocationIDs, while Rat Race uses nine gameplay
zones. This script keeps that translation explicit and reproducible instead of
embedding an opaque modulo or borough heuristic in the data query.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


ZONE_IDS = (
    "harlem",
    "upper_west",
    "upper_east",
    "midtown",
    "downtown",
    "north_brooklyn",
    "south_brooklyn",
    "queens_west",
    "airports",
)


def _contains(zone: str, *terms: str) -> bool:
    return any(term in zone for term in terms)


def classify_zone(borough: str, zone: str) -> str | None:
    """Classify one TLC neighborhood using documented MVP macro-zone rules."""

    borough = borough.strip().lower()
    zone = zone.strip().lower()

    if borough in {"unknown", "n/a"} or zone in {"n/a", "outside of nyc"}:
        return None
    if borough == "ewr" or "airport" in zone:
        return "airports"
    if borough == "bronx":
        return "harlem"
    if borough == "queens":
        return "queens_west"
    if borough == "staten island":
        return "south_brooklyn"
    if borough == "brooklyn":
        if _contains(
            zone,
            "bedford",
            "brooklyn heights",
            "brooklyn navy yard",
            "bushwick",
            "clinton hill",
            "downtown brooklyn",
            "dumbo",
            "east new york",
            "east williamsburg",
            "fort greene",
            "greenpoint",
            "ocean hill",
            "prospect heights",
            "stuyvesant heights",
            "williamsburg",
        ):
            return "north_brooklyn"
        return "south_brooklyn"

    # Manhattan is split by named neighborhood families. The remaining
    # central Manhattan names belong to the Midtown macro-zone.
    if _contains(
        zone,
        "harlem",
        "washington heights",
        "inwood",
        "marble hill",
        "hamilton heights",
        "highbridge park",
    ):
        return "harlem"
    if _contains(
        zone,
        "upper west",
        "lincoln square",
        "manhattan valley",
        "morningside",
        "bloomingdale",
    ):
        return "upper_west"
    if _contains(
        zone,
        "upper east",
        "yorkville",
        "lenox hill",
        "roosevelt island",
        "sutton place",
        "turtle bay",
        "stuy town",
        "randalls island",
        "un/turtle bay",
    ):
        return "upper_east"
    if _contains(
        zone,
        "alphabet city",
        "battery",
        "chinatown",
        "east village",
        "financial district",
        "governor",
        "greenwich village",
        "hudson sq",
        "little italy",
        "lower east",
        "meatpacking",
        "seaport",
        "soho",
        "tribeca",
        "two bridges",
        "west village",
        "world trade",
    ):
        return "downtown"
    return "midtown"


def build_map(lookup_path: Path, output_path: Path) -> int:
    with lookup_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mapped = 0
    with output_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=("LocationID", "game_zone", "source_borough", "source_zone"),
        )
        writer.writeheader()
        for row in rows:
            game_zone = classify_zone(row["Borough"], row["Zone"])
            if game_zone is None:
                continue
            if game_zone not in ZONE_IDS:
                raise ValueError(f"Unexpected game zone: {game_zone}")
            writer.writerow(
                {
                    "LocationID": int(row["LocationID"]),
                    "game_zone": game_zone,
                    "source_borough": row["Borough"],
                    "source_zone": row["Zone"],
                }
            )
            mapped += 1
    return mapped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Mapped {build_map(args.lookup, args.output)} TLC locations")


if __name__ == "__main__":
    main()
