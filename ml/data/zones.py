"""Build the (12) game zones and their geometries.

Downloaded once and cached:
  * ``taxi_zone_lookup.csv``  (TLC zone id -> borough / zone name)
  * ``taxi_zones.zip``        (zone polygons for centroids)

This script also encodes the hand-curated mapping of the 263 TLC zones into the
12 game zones. The mapping is an explicit ``LOCATION_ID_TO_ZONE`` table so it can
be audited by a human (rat-game.md requires this sanity check).
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from .. import config
from ..config import GAME_ZONES

# fmt: off
#: Explicit TLC LocationID -> game-zone slug. Missing keys are excluded from the game.
LOCATION_ID_TO_ZONE: dict[int, str] = {
    # ---- Manhattan ----
    41: "harlem", 42: "harlem", 43: "harlem", 74: "harlem", 75: "harlem",
    116: "harlem", 120: "harlem", 127: "harlem", 128: "harlem", 152: "harlem",
    153: "harlem", 166: "harlem", 194: "harlem", 202: "harlem", 243: "harlem",
    244: "harlem",
    24: "upper_west", 142: "upper_west", 143: "upper_west", 151: "upper_west", 238: "upper_west",
    239: "upper_west",
    137: "upper_east", 140: "upper_east", 141: "upper_east", 229: "upper_east", 233: "upper_east",
    236: "upper_east", 237: "upper_east", 262: "upper_east", 263: "upper_east",
    48: "midtown", 50: "midtown", 68: "midtown", 90: "midtown", 100: "midtown",
    107: "midtown", 125: "midtown", 161: "midtown", 162: "midtown", 163: "midtown",
    164: "midtown", 170: "midtown", 186: "midtown", 224: "midtown", 230: "midtown",
    234: "midtown", 246: "midtown",
    4: "downtown", 12: "downtown", 13: "downtown", 45: "downtown", 79: "downtown",
    87: "downtown", 88: "downtown", 103: "downtown", 104: "downtown", 105: "downtown",
    113: "downtown", 114: "downtown", 144: "downtown", 148: "downtown", 158: "downtown",
    209: "downtown", 211: "downtown", 231: "downtown", 232: "downtown", 249: "downtown",
    261: "downtown",
    # ---- Brooklyn ----
    17: "north_brooklyn", 25: "north_brooklyn", 33: "north_brooklyn", 34: "north_brooklyn", 36: "north_brooklyn",
    37: "north_brooklyn", 40: "north_brooklyn", 49: "north_brooklyn", 52: "north_brooklyn", 54: "north_brooklyn",
    61: "north_brooklyn", 62: "north_brooklyn", 63: "north_brooklyn", 65: "north_brooklyn", 66: "north_brooklyn",
    76: "north_brooklyn", 77: "north_brooklyn", 80: "north_brooklyn", 89: "north_brooklyn", 97: "north_brooklyn",
    106: "north_brooklyn", 112: "north_brooklyn", 149: "north_brooklyn", 177: "north_brooklyn", 181: "north_brooklyn",
    188: "north_brooklyn", 189: "north_brooklyn", 190: "north_brooklyn", 217: "north_brooklyn", 222: "north_brooklyn",
    225: "north_brooklyn", 255: "north_brooklyn", 256: "north_brooklyn", 257: "north_brooklyn",
    11: "south_brooklyn", 14: "south_brooklyn", 21: "south_brooklyn", 22: "south_brooklyn", 26: "south_brooklyn",
    29: "south_brooklyn", 35: "south_brooklyn", 39: "south_brooklyn", 55: "south_brooklyn", 67: "south_brooklyn",
    71: "south_brooklyn", 72: "south_brooklyn", 85: "south_brooklyn", 91: "south_brooklyn", 108: "south_brooklyn",
    111: "south_brooklyn", 123: "south_brooklyn", 133: "south_brooklyn", 150: "south_brooklyn", 154: "south_brooklyn",
    155: "south_brooklyn", 165: "south_brooklyn", 178: "south_brooklyn", 195: "south_brooklyn", 210: "south_brooklyn",
    227: "south_brooklyn", 228: "south_brooklyn",
    # ---- Queens (west / east / airports; EWR + JFK + LGA) ----
    7: "queens_west", 8: "queens_west", 9: "queens_west", 53: "queens_west", 56: "queens_west",
    57: "queens_west", 70: "queens_west", 73: "queens_west", 82: "queens_west", 83: "queens_west",
    92: "queens_west", 93: "queens_west", 95: "queens_west", 96: "queens_west", 98: "queens_west",
    102: "queens_west", 121: "queens_west", 129: "queens_west", 134: "queens_west", 135: "queens_west",
    145: "queens_west", 146: "queens_west", 157: "queens_west", 160: "queens_west", 171: "queens_west",
    173: "queens_west", 175: "queens_west", 179: "queens_west", 180: "queens_west", 192: "queens_west",
    193: "queens_west", 196: "queens_west", 197: "queens_west", 198: "queens_west", 207: "queens_west",
    223: "queens_west", 226: "queens_west", 252: "queens_west", 253: "queens_west", 258: "queens_west",
    260: "queens_west",
    2: "queens_east", 10: "queens_east", 15: "queens_east", 16: "queens_east", 19: "queens_east",
    27: "queens_east", 28: "queens_east", 30: "queens_east", 38: "queens_east", 64: "queens_east",
    86: "queens_east", 101: "queens_east", 117: "queens_east", 122: "queens_east", 124: "queens_east",
    130: "queens_east", 131: "queens_east", 139: "queens_east", 191: "queens_east", 201: "queens_east",
    203: "queens_east", 205: "queens_east", 215: "queens_east", 216: "queens_east", 218: "queens_east",
    219: "queens_east",
    1: "airports", 132: "airports", 138: "airports",   # EWR, JFK, LaGuardia
    # ---- Bronx ----
    3: "bronx", 18: "bronx", 20: "bronx", 31: "bronx", 32: "bronx",
    46: "bronx", 47: "bronx", 51: "bronx", 58: "bronx", 59: "bronx",
    60: "bronx", 69: "bronx", 78: "bronx", 81: "bronx", 94: "bronx",
    119: "bronx", 126: "bronx", 136: "bronx", 147: "bronx", 159: "bronx",
    167: "bronx", 168: "bronx", 169: "bronx", 174: "bronx", 182: "bronx",
    183: "bronx", 184: "bronx", 185: "bronx", 199: "bronx", 200: "bronx",
    208: "bronx", 212: "bronx", 213: "bronx", 220: "bronx", 235: "bronx",
    240: "bronx", 241: "bronx", 242: "bronx", 247: "bronx", 248: "bronx",
    250: "bronx", 254: "bronx", 259: "bronx",
    # ---- Staten Island ----
    5: "staten_island", 6: "staten_island", 23: "staten_island", 44: "staten_island", 84: "staten_island",
    99: "staten_island", 109: "staten_island", 110: "staten_island", 115: "staten_island", 118: "staten_island",
    156: "staten_island", 172: "staten_island", 176: "staten_island", 187: "staten_island", 204: "staten_island",
    206: "staten_island", 214: "staten_island", 221: "staten_island", 245: "staten_island", 251: "staten_island",
}
# fmt: on

# Reserved/Junk TLC ids with no real zone (id 264 borough "Unknown", id 265
# "Outside of NYC"). Every other TLC zone -- including Newark Airport (EWR) --
# maps to one of the 12 game zones, so no borough is excluded.
EXCLUDED_LOCATION_IDS: set[int] = {264, 265}
EXCLUDED_BOROUGHS: set[str] = set()


def _download(url: str, path, must_exist=True) -> bool:
    if path.exists() and path.stat().st_size > 0:
        return True
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.content:
                path.write_bytes(r.content)
                return True
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    if must_exist:
        raise RuntimeError(f"could not download {url}")
    return False


def load_lookup() -> pd.DataFrame:
    """TLC zone lookup table (cached)."""
    _download(config.LOOKUP_CSV_URL, config.LOOKUP_CSV)
    df = pd.read_csv(config.LOOKUP_CSV)
    df["LocationID"] = df["LocationID"].astype(int)
    return df


def load_zone_geom() -> dict[int, object]:
    """Extract taxi-zones shapefile and return {LocationID: shapely MultiPolygon}.

    The shapefile is in EPSG:2263 (NAD83 / NY Long Island, US ft); keep the
    polygons in those units and project only the final centroids to lat/lon.
    """
    import io
    import zipfile

    import shapefile as pyshp

    _download(config.ZONES_SHP_ZIP_URL, config.ZONES_SHP_ZIP)
    zf = zipfile.ZipFile(config.ZONES_SHP_ZIP)
    shp_bytes = zf.read(next(n for n in zf.namelist() if n.endswith(".shp")))
    shx_bytes = zf.read(next(n for n in zf.namelist() if n.endswith(".shx")))
    dbf_bytes = zf.read(next(n for n in zf.namelist() if n.endswith(".dbf")))

    from shapely.geometry import shape

    sf = pyshp.Reader(
        shp=io.BytesIO(shp_bytes), shx=io.BytesIO(shx_bytes), dbf=io.BytesIO(dbf_bytes)
    )
    loc_field = next((f[0] for f in sf.fields[1:] if f[0].upper() == "LOCATIONID"), None)
    if loc_field is None:
        raise RuntimeError("LocationID field not found in taxi_zones.dbf")
    geom: dict[int, object] = {}
    for i in range(len(sf)):
        loc = int(sf.record(i)[loc_field])
        geom[loc] = shape(sf.shape(i).__geo_interface__)
    return geom


def build_zone_map(lookup: pd.DataFrame) -> pd.DataFrame:
    """(loc_id, borough, zone_name, game_zone) for every TLC zone."""
    records = []
    for _, row in lookup.iterrows():
        loc = int(row["LocationID"])
        borough = row["Borough"]
        if loc in LOCATION_ID_TO_ZONE:
            game = LOCATION_ID_TO_ZONE[loc]
        elif loc in EXCLUDED_LOCATION_IDS or borough in EXCLUDED_BOROUGHS:
            game = None
        else:
            # Every loc in the lookup must be explicitly accounted for.
            raise ValueError(f"location {loc} ({row['Zone']}) not covered in mapping")
        records.append(
            {"loc_id": loc, "borough": borough, "zone_name": row["Zone"], "game_zone": game}
        )
    df = pd.DataFrame(records)
    assert set(df.loc_id) == set(range(1, 266)), f"missing ids: {set(range(1, 266)) - set(df.loc_id)}"
    return df


def build_game_geometry(zone_map: pd.DataFrame) -> pd.DataFrame:
    """One row per game zone: centroid(lat, lon) from its members' polygons."""
    from pyproj import Transformer
    from shapely.geometry import MultiPolygon

    geom = load_zone_geom()
    transformer = Transformer.from_crs(config.ZONES_SHP_EPSG, "EPSG:4326", always_xy=True)

    rows = []
    for slug, name in GAME_ZONES.items():
        member_locs = zone_map.loc[zone_map.game_zone == slug, "loc_id"].tolist()
        polys = [g for loc in member_locs if (g := geom.get(loc)) is not None]
        missing = sorted(set(member_locs) - set(geom))
        if missing:
            print(f"[warn] {slug}: no geometry for ids {missing}")
        if not polys:
            raise RuntimeError(f"no geometry for game zone {slug}")
        from shapely.ops import unary_union

        cent = unary_union(polys).centroid
        lon, lat = transformer.transform(cent.x, cent.y)
        rows.append(
            {
                "game_zone": slug,
                "display_name": name,
                "n_zones": len(polys),
                "centroid_lat": float(lat),
                "centroid_lon": float(lon),
                "member_loc_ids": member_locs,
            }
        )
    return pd.DataFrame(rows)


def build_neighbors(game_geo: pd.DataFrame, k: int = 2) -> pd.DataFrame:
    """k-nearest other game zones by great-circle distance from each centroid."""
    from math import asin, cos, radians, sin, sqrt

    lat, lon = game_geo.centroid_lat.to_numpy(), game_geo.centroid_lon.to_numpy()

    def hav(i, j):
        dlat = radians(lat[j] - lat[i])
        dlon = radians(lon[j] - lon[i])
        a = sin(dlat / 2) ** 2 + cos(radians(lat[i])) * cos(radians(lat[j])) * sin(dlon / 2) ** 2
        return 6371.0 * 2 * asin(sqrt(a))

    n = len(game_geo)
    rows = []
    for i in range(n):
        dists = sorted(
            ((j, hav(i, j)) for j in range(n) if j != i), key=lambda t: t[1]
        )[:k]
        for j, d in dists:
            rows.append(
                {
                    "game_zone": game_geo.game_zone.iloc[i],
                    "neighbor": game_geo.game_zone.iloc[j],
                    "distance_km": round(float(d), 2),
                }
            )
    return pd.DataFrame(rows)


def main(force: bool = False) -> None:
    lookup = load_lookup()
    zone_map = build_zone_map(lookup)
    zone_map.to_parquet(config.ML_ARTIFACTS / "zone_map.parquet", index=False)

    game_geo = build_game_geometry(zone_map)
    game_geo.to_parquet(config.ML_ARTIFACTS / "game_zones.parquet", index=False)

    neighbors = build_neighbors(game_geo)
    neighbors.to_parquet(config.ML_ARTIFACTS / "zone_neighbors.parquet", index=False)

    print(f"game zones: {len(game_geo)} | neighbors: {len(neighbors)}")
    print(game_geo[["game_zone", "n_zones", "centroid_lat", "centroid_lon"]].to_string(index=False))


if __name__ == "__main__":
    main()