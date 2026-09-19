import io
import zipfile
import requests
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse

# ============================================================
# CONFIG
# ============================================================

START_YEAR = 2015
END_YEAR = 2026

# Broad NYC metro-area bounding box
LAT_MIN, LAT_MAX = 40.45, 41.05
LON_MIN, LON_MAX = -74.30, -73.55

BASE_OUT = Path("gdelt_nyc")

EVENTS_OUT = BASE_OUT / "events"
MENTIONS_OUT = BASE_OUT / "mentions"
GKG_OUT = BASE_OUT / "gkg"

for p in [EVENTS_OUT, MENTIONS_OUT, GKG_OUT]:
    p.mkdir(parents=True, exist_ok=True)

MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"

session = requests.Session()
session.headers.update({
    "User-Agent": "NYC-Rat-Race/1.0"
})


# ============================================================
# COLUMN DEFINITIONS
# ============================================================

EVENT_COLUMNS = [
    "GLOBALEVENTID",
    "SQLDATE",
    "MonthYear",
    "Year",
    "FractionDate",

    "Actor1Code",
    "Actor1Name",
    "Actor1CountryCode",
    "Actor1KnownGroupCode",
    "Actor1EthnicCode",
    "Actor1Religion1Code",
    "Actor1Religion2Code",
    "Actor1Type1Code",
    "Actor1Type2Code",
    "Actor1Type3Code",

    "Actor2Code",
    "Actor2Name",
    "Actor2CountryCode",
    "Actor2KnownGroupCode",
    "Actor2EthnicCode",
    "Actor2Religion1Code",
    "Actor2Religion2Code",
    "Actor2Type1Code",
    "Actor2Type2Code",
    "Actor2Type3Code",

    "IsRootEvent",

    "EventCode",
    "EventBaseCode",
    "EventRootCode",
    "QuadClass",
    "GoldsteinScale",

    "NumMentions",
    "NumSources",
    "NumArticles",
    "AvgTone",

    "Actor1Geo_Type",
    "Actor1Geo_FullName",
    "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code",
    "Actor1Geo_Lat",
    "Actor1Geo_Long",
    "Actor1Geo_FeatureID",

    "Actor2Geo_Type",
    "Actor2Geo_FullName",
    "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code",
    "Actor2Geo_Lat",
    "Actor2Geo_Long",
    "Actor2Geo_FeatureID",

    "ActionGeo_Type",
    "ActionGeo_FullName",
    "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code",
    "ActionGeo_Lat",
    "ActionGeo_Long",
    "ActionGeo_FeatureID",

    "DATEADDED",
    "SOURCEURL",
]


MENTION_COLUMNS = [
    "GLOBALEVENTID",
    "EventTimeDate",
    "MentionTimeDate",
    "MentionType",
    "MentionSourceName",
    "MentionIdentifier",
    "SentenceID",
    "Actor1CharOffset",
    "Actor2CharOffset",
    "ActionCharOffset",
    "InRawText",
    "Confidence",
    "MentionDocLen",
    "MentionDocTone",
    "MentionDocTranslationInfo",
    "Extras",
]


GKG_COLUMNS = [
    "GKGRECORDID",
    "V2DATE",
    "V2SOURCECOLLECTIONIDENTIFIER",
    "V2SOURCECOMMONNAME",
    "V2DOCUMENTIDENTIFIER",
    "V1COUNTS",
    "V2COUNTS",
    "V1THEMES",
    "V2ENHANCEDTHEMES",
    "V1LOCATIONS",
    "V2ENHANCEDLOCATIONS",
    "V1PERSONS",
    "V2ENHANCEDPERSONS",
    "V1ORGANIZATIONS",
    "V2ENHANCEDORGANIZATIONS",
    "V1TONE",
    "V2ENHANCEDDATES",
    "V2GCAM",
    "V2SHARINGIMAGE",
    "V2RELATEDIMAGES",
    "V2SOCIALIMAGEEMBEDS",
    "V2SOCIALVIDEOEMBEDS",
    "V2QUOTATIONS",
    "V2ALLNAMES",
    "V2AMOUNTS",
    "V2TRANSLATIONINFO",
    "V2EXTRASXML",
]


# ============================================================
# HELPERS
# ============================================================

def year_from_filename(url):
    """
    Extract YYYY from filenames like:
    20150219150000.export.CSV.zip
    """
    filename = Path(urlparse(url).path).name

    try:
        return int(filename[:4])
    except ValueError:
        return None


def download_zip_dataframe(url, columns):
    """
    Downloads one GDELT ZIP into memory, opens the TSV,
    and returns a dataframe.

    Nothing is permanently written to disk.
    """

    r = session.get(url, timeout=180)
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()

        if not names:
            return None

        with z.open(names[0]) as f:
            return pd.read_csv(
                f,
                sep="\t",
                names=columns,
                header=None,
                dtype=str,
                low_memory=False,
                on_bad_lines="skip",
            )


def save_append(df, path):
    """
    Append data to one CSV file.

    This means you don't end up with tens of thousands
    of tiny files.
    """

    if df.empty:
        return

    exists = path.exists()

    df.to_csv(
        path,
        mode="a",
        header=not exists,
        index=False,
    )


# ============================================================
# GET MASTER FILE LIST
# ============================================================

print("Downloading GDELT master file list...")

r = session.get(MASTER_URL, timeout=60)
r.raise_for_status()

event_files = []
mention_files = []
gkg_files = []

for line in r.text.splitlines():

    parts = line.split()

    if len(parts) < 3:
        continue

    url = parts[-1]

    year = year_from_filename(url)

    if year is None:
        continue

    if not START_YEAR <= year <= END_YEAR:
        continue

    if url.endswith(".export.CSV.zip"):
        event_files.append(url)

    elif url.endswith(".mentions.CSV.zip"):
        mention_files.append(url)

    elif url.endswith(".gkg.csv.zip"):
        gkg_files.append(url)


print(f"Events files:   {len(event_files):,}")
print(f"Mentions files: {len(mention_files):,}")
print(f"GKG files:      {len(gkg_files):,}")


# ============================================================
# PROCESS EVENTS
# ============================================================

print("\n========================================")
print("PROCESSING EVENTS")
print("========================================")

nyc_event_ids = set()

for i, url in enumerate(event_files, 1):

    filename = Path(urlparse(url).path).name

    print(
        f"[Events {i:,}/{len(event_files):,}] "
        f"{filename}"
    )

    try:

        df = download_zip_dataframe(
            url,
            EVENT_COLUMNS,
        )

        if df is None or df.empty:
            continue

        # Convert geo fields
        df["ActionGeo_Lat"] = pd.to_numeric(
            df["ActionGeo_Lat"],
            errors="coerce"
        )

        df["ActionGeo_Long"] = pd.to_numeric(
            df["ActionGeo_Long"],
            errors="coerce"
        )

        # NYC geographic filter
        mask = (
            df["ActionGeo_Lat"].between(
                LAT_MIN,
                LAT_MAX
            )
            &
            df["ActionGeo_Long"].between(
                LON_MIN,
                LON_MAX
            )
        )

        nyc = df.loc[mask].copy()

        if nyc.empty:
            continue

        # Save event IDs so Mentions can be matched later
        nyc_event_ids.update(
            nyc["GLOBALEVENTID"]
            .dropna()
            .astype(str)
        )

        keep = [
            "GLOBALEVENTID",
            "SQLDATE",
            "Year",

            "Actor1Code",
            "Actor1Name",
            "Actor1CountryCode",

            "Actor2Code",
            "Actor2Name",
            "Actor2CountryCode",

            "IsRootEvent",

            "EventCode",
            "EventBaseCode",
            "EventRootCode",
            "QuadClass",

            "GoldsteinScale",

            "NumMentions",
            "NumSources",
            "NumArticles",
            "AvgTone",

            "ActionGeo_FullName",
            "ActionGeo_CountryCode",
            "ActionGeo_ADM1Code",
            "ActionGeo_Lat",
            "ActionGeo_Long",

            "DATEADDED",
            "SOURCEURL",
        ]

        nyc = nyc[keep]

        year = year_from_filename(url)

        save_append(
            nyc,
            EVENTS_OUT / f"gdelt_events_nyc_{year}.csv"
        )

        print(
            f"    kept {len(nyc):,} NYC events"
        )

    except Exception as e:
        print(f"    ERROR: {e}")


print(
    f"\nUnique NYC event IDs: "
    f"{len(nyc_event_ids):,}"
)


# ============================================================
# PROCESS MENTIONS
# ============================================================

print("\n========================================")
print("PROCESSING MENTIONS")
print("========================================")

for i, url in enumerate(mention_files, 1):

    filename = Path(urlparse(url).path).name

    print(
        f"[Mentions {i:,}/{len(mention_files):,}] "
        f"{filename}"
    )

    try:

        df = download_zip_dataframe(
            url,
            MENTION_COLUMNS,
        )

        if df is None or df.empty:
            continue

        # Only mentions belonging to NYC events
        nyc = df[
            df["GLOBALEVENTID"]
            .astype(str)
            .isin(nyc_event_ids)
        ].copy()

        if nyc.empty:
            continue

        keep = [
            "GLOBALEVENTID",
            "EventTimeDate",
            "MentionTimeDate",

            "MentionType",
            "MentionSourceName",
            "MentionIdentifier",

            "SentenceID",
            "Confidence",

            "MentionDocLen",
            "MentionDocTone",

            "MentionDocTranslationInfo",
        ]

        nyc = nyc[keep]

        year = year_from_filename(url)

        save_append(
            nyc,
            MENTIONS_OUT / f"gdelt_mentions_nyc_{year}.csv"
        )

        print(
            f"    kept {len(nyc):,} mentions"
        )

    except Exception as e:
        print(f"    ERROR: {e}")


# ============================================================
# PROCESS GKG
# ============================================================

print("\n========================================")
print("PROCESSING GKG")
print("========================================")

# Text patterns indicating NYC.
#
# GKG locations use structured semicolon/hash-separated
# strings, so broad matching works well enough here.
NYC_TERMS = [
    "New York, New York",
    "New York City",
    "Manhattan",
    "Brooklyn",
    "Queens",
    "Bronx",
    "Staten Island",
]


for i, url in enumerate(gkg_files, 1):

    filename = Path(urlparse(url).path).name

    print(
        f"[GKG {i:,}/{len(gkg_files):,}] "
        f"{filename}"
    )

    try:

        df = download_zip_dataframe(
            url,
            GKG_COLUMNS,
        )

        if df is None or df.empty:
            continue

        locations = (
            df["V2ENHANCEDLOCATIONS"]
            .fillna("")
            .astype(str)
        )

        mask = pd.Series(
            False,
            index=df.index
        )

        for term in NYC_TERMS:
            mask |= locations.str.contains(
                term,
                case=False,
                regex=False,
            )

        nyc = df.loc[mask].copy()

        if nyc.empty:
            continue

        keep = [
            "GKGRECORDID",
            "V2DATE",

            "V2SOURCECOLLECTIONIDENTIFIER",
            "V2SOURCECOMMONNAME",
            "V2DOCUMENTIDENTIFIER",

            "V1COUNTS",
            "V2COUNTS",

            "V1THEMES",
            "V2ENHANCEDTHEMES",

            "V1LOCATIONS",
            "V2ENHANCEDLOCATIONS",

            "V1PERSONS",
            "V2ENHANCEDPERSONS",

            "V1ORGANIZATIONS",
            "V2ENHANCEDORGANIZATIONS",

            "V1TONE",

            "V2ENHANCEDDATES",
            "V2GCAM",

            "V2QUOTATIONS",
            "V2ALLNAMES",
            "V2AMOUNTS",

            "V2TRANSLATIONINFO",
        ]

        nyc = nyc[keep]

        year = year_from_filename(url)

        save_append(
            nyc,
            GKG_OUT / f"gdelt_gkg_nyc_{year}.csv"
        )

        print(
            f"    kept {len(nyc):,} NYC GKG records"
        )

    except Exception as e:
        print(f"    ERROR: {e}")


print("\n========================================")
print("DONE")
print("========================================")
print(f"Output directory: {BASE_OUT.resolve()}")
