"""Step 3 of 3 — zonal statistics, the vulnerability index, and the artifact.

Reads the neighborhood table from boundaries_acs.py and the rasters from
landsat.py, reduces every raster to one number per neighborhood, builds the
composite index, and writes data/neighborhoods.json.

That file is the whole contract with the dashboard. Nothing downstream reads
anything else.

    python pipeline/join_export.py
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
IN_PATH = DATA / "neighborhoods_acs.parquet"
OUT_JSON = DATA / "neighborhoods.json"
SITE_JSON = ROOT / "site" / "neighborhoods.json"

# Raster -> output column. Every one is a mean over the neighborhood's pixels.
RASTERS = {
    "lst_anomaly": "heat_anomaly",
    "lst_absolute": "heat_absolute",
    "ndvi_median": "ndvi",
    "fog_frequency": "fog",
    "clear_obs_count": "clear_obs",
}

# Simplify in metres before reprojecting. 15 m is invisible at city zoom and
# cuts the payload by roughly an order of magnitude.
SIMPLIFY_M = 15
COORD_PRECISION = 5

# The index. Each entry is (column, direction) where +1 means "more is worse"
# and -1 means "more is better". Race and ethnicity are deliberately absent —
# they are the overlay the index gets compared against, not an input to it.
COMPONENTS = {
    "exposure": [
        ("heat_anomaly", +1),
        ("ndvi", -1),  # vegetation shades and cools
        ("fog", -1),  # marine fog is free air conditioning
    ],
    "sensitivity": [
        ("pct_65_plus", +1),
        ("pct_under_5", +1),
        ("pct_poverty", +1),
    ],
    "capacity_gap": [
        ("median_income", -1),
        ("pct_renter", +1),  # renters often cannot install cooling
        ("pct_limited_english", +1),  # heat warnings may not reach them
    ],
}


def zonal_means(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """One mean per neighborhood per raster.

    Deliberately not exactextract: at 30 m against neighborhoods of several
    square kilometres, fractional edge pixels move nothing, and this keeps the
    dependency list shorter.
    """
    first = DATA / "lst_anomaly.tif"
    with rasterio.open(first) as src:
        transform, width, height, crs = src.transform, src.width, src.height, src.crs

    aligned = gdf.to_crs(crs)
    labels = rasterize(
        [(geom, i + 1) for i, geom in enumerate(aligned.geometry)],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="int32",
    )

    results: dict[str, list] = {name: [] for name in RASTERS.values()}
    results["pixels"] = []

    stacks = {}
    for stem, column in RASTERS.items():
        with rasterio.open(DATA / f"{stem}.tif") as src:
            stacks[column] = src.read(1)

    p90 = []
    for i in range(len(aligned)):
        mask = labels == i + 1
        for column, array in stacks.items():
            values = array[mask]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                results[column].append(np.nanmean(values))
        heat = stacks["heat_anomaly"][mask]
        heat = heat[~np.isnan(heat)]
        p90.append(np.percentile(heat, 90) if heat.size else np.nan)
        results["pixels"].append(int(mask.sum()))

    out = pd.DataFrame(results)
    out["heat_p90"] = p90
    return out


def percentile_rank(series: pd.Series, direction: int) -> pd.Series:
    """Rank into (0, 1], oriented so that higher always means worse.

    pandas puts the lowest value at 1/n rather than 0, which matters: a zero
    would annihilate the geometric mean below.
    """
    return (series * direction).rank(pct=True)


def build_index(df: pd.DataFrame) -> pd.DataFrame:
    scored = df["has_residents"] & df["heat_anomaly"].notna()

    for name, parts in COMPONENTS.items():
        ranks = [
            percentile_rank(df.loc[scored, column], direction)
            for column, direction in parts
        ]
        df.loc[scored, name] = 100 * pd.concat(ranks, axis=1).mean(axis=1)

    # Geometric mean across the three components, not arithmetic. Multiplicative
    # means a neighborhood cannot fully buy off extreme exposure with strong
    # adaptive capacity — which is the entire point of a vulnerability index.
    parts = df.loc[scored, list(COMPONENTS)]
    df.loc[scored, "index"] = np.exp(np.log(parts).mean(axis=1))
    df.loc[scored, "rank"] = df.loc[scored, "index"].rank(ascending=False).astype(int)
    return df


def round_coords(obj, precision: int = COORD_PRECISION):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), precision) for v in obj]
        return [round_coords(part, precision) for part in obj]
    return obj


def to_geojson(gdf: gpd.GeoDataFrame, columns: list[str]) -> dict:
    features = []
    for _, row in gdf.iterrows():
        props = {}
        for column in columns:
            value = row[column]
            if pd.isna(value):
                props[column] = None
            elif isinstance(value, str):
                props[column] = value
            elif isinstance(value, (bool, np.bool_)):
                props[column] = bool(value)
            elif isinstance(value, (int, np.integer)):
                props[column] = int(value)
            else:
                props[column] = round(float(value), 2)
        geometry = mapping(row.geometry)
        geometry["coordinates"] = round_coords(geometry["coordinates"])
        features.append(
            {"type": "Feature", "properties": props, "geometry": geometry}
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    gdf = gpd.read_parquet(IN_PATH)
    print(f"Loaded {len(gdf)} neighborhoods from {IN_PATH.name}")

    print("\nZonal statistics...")
    stats = zonal_means(gdf)
    gdf = pd.concat([gdf.reset_index(drop=True), stats], axis=1)
    print(f"  {int(gdf['pixels'].sum()):,} pixels assigned to neighborhoods")
    print(f"  NDVI range {gdf['ndvi'].min():.2f} to {gdf['ndvi'].max():.2f}")
    print(f"  fog range  {100 * gdf['fog'].min():.0f}% to {100 * gdf['fog'].max():.0f}%")
    print(f"  heat anomaly {gdf['heat_anomaly'].min():+.1f} to "
          f"{gdf['heat_anomaly'].max():+.1f} C")

    gdf = build_index(gdf)
    scored = gdf["index"].notna()
    print(f"\n  scored {int(scored.sum())} of {len(gdf)} neighborhoods")

    # --- Does the story hold up? ------------------------------------------
    ok = gdf[scored]
    print("\nRelationships (Pearson r):")
    for a, b in (
        ("heat_anomaly", "median_income"),
        ("heat_anomaly", "ndvi"),
        ("heat_anomaly", "fog"),
        ("index", "pct_poc"),
        ("fog", "median_income"),
    ):
        r = ok[a].corr(ok[b])
        print(f"    {a:<14} vs {b:<16} r = {r:+.2f}")

    # --- Export ------------------------------------------------------------
    export = gdf.copy()
    export["geometry"] = export.geometry.simplify(SIMPLIFY_M, preserve_topology=True)
    export = export.to_crs(4326)

    columns = [
        "nhood", "population", "households", "has_residents",
        "median_income", "pct_poverty", "pct_65_plus", "pct_under_5",
        "pct_renter", "pct_limited_english",
        "pct_white_nh", "pct_black_nh", "pct_asian_nh", "pct_hispanic", "pct_poc",
        "heat_anomaly", "heat_p90", "heat_absolute", "ndvi", "fog", "clear_obs",
        "exposure", "sensitivity", "capacity_gap", "index", "rank",
    ]

    geojson = to_geojson(export, columns)
    payload = json.dumps(geojson, allow_nan=False, separators=(",", ":"))

    OUT_JSON.write_text(payload, encoding="utf-8")
    SITE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SITE_JSON.write_text(payload, encoding="utf-8")

    # Also as a script file. fetch() is blocked under file://, so shipping the
    # data as JS means the page opens by double-clicking it — no server, no
    # build step — and still keeps data out of the HTML.
    (SITE_JSON.parent / "data.js").write_text(
        "const NEIGHBORHOODS = " + payload + ";\n", encoding="utf-8"
    )
    print(f"\n  wrote {OUT_JSON.name}, site/{SITE_JSON.name}, site/data.js "
          f"({len(payload) / 1024:.0f} KB)")

    # --- Report ------------------------------------------------------------
    show = ["rank", "nhood", "index", "heat_anomaly", "ndvi", "fog",
            "median_income", "pct_poverty"]
    table = gdf[scored][show].sort_values("rank")
    pd.set_option("display.width", 140)
    fmt = lambda v: f"{v:,.1f}"  # noqa: E731
    print("\nMost heat-vulnerable:")
    print(table.head(8).to_string(index=False, float_format=fmt))
    print("\nLeast heat-vulnerable:")
    print(table.tail(5).to_string(index=False, float_format=fmt))


if __name__ == "__main__":
    main()
