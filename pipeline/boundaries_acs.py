"""Step 1 of 3 — neighborhoods and demographics.

Builds the 41-row table that everything else joins onto: San Francisco Analysis
Neighborhood polygons with American Community Survey (ACS) equity variables
attached.

Run before landsat.py and join_export.py.

    python pipeline/boundaries_acs.py

Downloads are cached under data/raw/, so re-runs are free. Delete that folder to
force a refresh.
"""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "neighborhoods_acs.parquet"

ANALYSIS_CRS = 3310  # California Albers, meters, EQUAL-AREA. All area/distance math.
STORAGE_CRS = 4326  # WGS84. What the downloads arrive in.

NHOOD_URL = "https://data.sfgov.org/resource/j2bu-swwd.geojson?$limit=100"
CROSSWALK_URL = (
    "https://data.sfgov.org/resource/sevw-6tgi.json"
    "?$limit=1000&$select=geoid,neighborhoods_analysis_boundaries"
)
# One request, every table. Census Reporter needs no API key; the Census Bureau's
# own endpoint does, and the key issued for this project never activated.
ACS_URL = (
    "https://api.censusreporter.org/1.0/data/show/latest"
    "?table_ids=B01003,B19013,B17001,B01001,B25003,C16002"
    "&geo_ids=140|05000US06075"
)

# Legally San Francisco County, 30 km offshore, and absent from the boundary
# file. Dropping it is an analytical choice, not cleanup — say so in the README.
EXCLUDE_NEIGHBORHOOD = "The Farallones"

# Three of the 41 analysis neighborhoods are parks with almost no residents:
# Golden Gate Park (58), McLaren Park (146), Lincoln Park (160). Their ACS rates
# are noise — McLaren Park reads 75% poverty and 94% over-65 off ~115 households,
# which would top any vulnerability ranking for no real reason, and Lincoln Park
# has zero households so its income and poverty are undefined outright.
#
# They are flagged rather than deleted: the index and every ranking skip them,
# but the polygons still render so the map covers the whole city.
#
# The gap is wide enough that the exact cutoff does not matter — the next
# smallest neighborhood is the Presidio at 3,901 residents, a 24x jump.
MIN_POPULATION = 500

POPULATION = "B01003001"
HOUSEHOLDS = "B25003001"
MEDIAN_INCOME = "B19013001"

# Every rate is (numerator columns, denominator column). Column meanings were
# read off the ACS table definitions, not assumed — see the table titles in
# PROJECT_PLAN.md.
AGE_65_PLUS = [f"B01001{n:03d}" for n in (20, 21, 22, 23, 24, 25, 44, 45, 46, 47, 48, 49)]

RATES: dict[str, tuple[list[str], str]] = {
    "pct_under_5": (["B01001003", "B01001027"], "B01001001"),
    "pct_65_plus": (AGE_65_PLUS, "B01001001"),
    "pct_poverty": (["B17001002"], "B17001001"),
    "pct_renter": (["B25003003"], HOUSEHOLDS),
    "pct_limited_english": (
        ["C16002004", "C16002007", "C16002010", "C16002013"],
        "C16002001",
    ),
}


# Census Reporter rejects the default "Python-urllib" agent with a 403, so
# identify the project properly rather than pretending to be a browser.
USER_AGENT = "sf-heat-equity/1.0 (portfolio project; contact via repo)"


def fetch(url: str, cache_name: str) -> bytes:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / cache_name
    if path.exists():
        print(f"  cached    {cache_name}")
        return path.read_bytes()
    print(f"  fetching  {cache_name}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        blob = response.read()
    path.write_bytes(blob)
    return blob


def load_neighborhoods() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(io.BytesIO(fetch(NHOOD_URL, "neighborhoods.geojson")))
    gdf = gdf[["nhood", "geometry"]].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    if gdf.crs is None:
        gdf = gdf.set_crs(STORAGE_CRS)
    return gdf.to_crs(ANALYSIS_CRS)


def load_crosswalk() -> pd.DataFrame:
    rows = json.loads(fetch(CROSSWALK_URL, "crosswalk.json"))
    df = pd.DataFrame(rows).rename(
        columns={"neighborhoods_analysis_boundaries": "nhood"}
    )
    return df[["geoid", "nhood"]]


def load_acs() -> tuple[pd.DataFrame, str]:
    """Tract estimates plus their margins of error.

    The margins are the whole basis for saying how much any of this can be
    trusted at tract level, where small populations produce very wide intervals.
    Error columns are prefixed `moe_`.
    """
    payload = json.loads(fetch(ACS_URL, "acs_tracts.json"))
    records = []
    for geo_id, tables in payload["data"].items():
        # "14000US06075010101" -> "06075010101", the tract GEOID the crosswalk uses.
        row: dict[str, object] = {"geoid": geo_id[-11:]}
        for table in tables.values():
            row.update(table["estimate"])
            row.update({"moe_" + k: v for k, v in table.get("error", {}).items()})
        records.append(row)
    return pd.DataFrame(records), payload["release"]["name"]


def main() -> None:
    print("Loading sources...")
    nhoods = load_neighborhoods()
    crosswalk = load_crosswalk()
    acs, release = load_acs()
    print(f"\n  ACS release: {release}")
    print(f"  neighborhoods {len(nhoods)}   crosswalk tracts {len(crosswalk)}   "
          f"ACS tracts {len(acs)}")

    # --- Drop the Farallones, then join -----------------------------------
    keep = crosswalk[crosswalk["nhood"] != EXCLUDE_NEIGHBORHOOD]
    print(f"\n  dropped {len(crosswalk) - len(keep)} tract "
          f"({EXCLUDE_NEIGHBORHOOD})  ->  {len(keep)} tracts")

    tracts = keep.merge(acs, on="geoid", how="left")
    missing = tracts[tracts[POPULATION].isna()]["geoid"].tolist()
    if missing:
        print(f"  WARNING: {len(missing)} tracts had no ACS data: {missing[:5]}")

    extras = sorted(set(acs["geoid"]) - set(keep["geoid"]))
    if extras:
        pops = acs.set_index("geoid").loc[extras, POPULATION]
        print(f"  {len(extras)} ACS tracts not in the crosswalk, populations "
              f"{list(pops.astype('Int64'))} — dropped")

    # --- Aggregate to neighborhoods ---------------------------------------
    # Rates are summed as numerator/denominator, never averaged across tracts.
    # That IS the population weighting: a 400-person tract contributes 400 people
    # to its neighborhood's total, not half the answer.
    count_cols = sorted(
        {POPULATION, HOUSEHOLDS}
        | {c for nums, den in RATES.values() for c in (*nums, den)}
    )
    for col in count_cols:
        tracts[col] = pd.to_numeric(tracts[col], errors="coerce").fillna(0)

    # A median cannot be summed or averaged honestly. The best available
    # approximation is a household-weighted mean of the tract medians, and it is
    # an approximation — flagged in the README, not smuggled through.
    tracts[MEDIAN_INCOME] = pd.to_numeric(tracts[MEDIAN_INCOME], errors="coerce")
    has_income = tracts[MEDIAN_INCOME].notna()
    tracts["_income_num"] = (tracts[MEDIAN_INCOME] * tracts[HOUSEHOLDS]).where(has_income, 0)
    tracts["_income_den"] = tracts[HOUSEHOLDS].where(has_income, 0)

    # Margins of error add in quadrature across tracts, not linearly — summing
    # them would badly overstate the uncertainty of an aggregate.
    moe_cols = []
    for col in count_cols:
        source = "moe_" + col
        target = "_var_" + col
        if source in tracts.columns:
            tracts[target] = pd.to_numeric(tracts[source], errors="coerce").fillna(0) ** 2
        else:
            tracts[target] = 0.0
        moe_cols.append(target)

    grouped = tracts.groupby("nhood", as_index=False)[
        count_cols + moe_cols + ["_income_num", "_income_den"]
    ].sum()
    grouped["tract_count"] = tracts.groupby("nhood").size().values

    out = pd.DataFrame({
        "nhood": grouped["nhood"],
        "tract_count": grouped["tract_count"],
        "population": grouped[POPULATION].astype(int),
        "households": grouped[HOUSEHOLDS].astype(int),
    })

    # Each rate carries its own coefficient of variation, so the dashboard can
    # say how much to trust it rather than presenting every number as equally
    # solid. The Census Bureau's own thresholds: CV under 0.15 is reliable,
    # 0.15-0.30 use with caution, above 0.30 unreliable.
    cvs = pd.DataFrame(index=grouped.index)
    for name, (nums, den) in RATES.items():
        numerator = grouped[nums].sum(axis=1)
        denominator = grouped[den]
        proportion = (numerator / denominator).where(denominator > 0)
        out[name] = 100 * proportion

        moe_num_sq = grouped[["_var_" + c for c in nums]].sum(axis=1)
        moe_den_sq = grouped["_var_" + den]
        radicand = moe_num_sq - (proportion**2) * moe_den_sq
        # The standard ACS ratio formula goes imaginary when the numerator is a
        # large share of the denominator; the Bureau's documented fallback is to
        # add rather than subtract, which is conservative.
        safe = np.where(
            radicand >= 0,
            np.sqrt(radicand.clip(lower=0)),
            np.sqrt(moe_num_sq + (proportion**2) * moe_den_sq),
        )
        moe_p = pd.Series(safe, index=grouped.index) / denominator
        cvs[name] = ((moe_p / 1.645) / proportion).where(proportion > 0)

    # Summarise with the median rather than the max. The max is always
    # pct_under_5 — a 1-6% population share whose margin is inherently enormous
    # — so a max-based flag would call two thirds of the city "unreliable" on the
    # strength of one of nine index inputs. Both are published, plus the
    # per-variable margins, so nothing is buried.
    out["acs_cv"] = cvs.median(axis=1)
    out["acs_cv_worst"] = cvs.max(axis=1)
    for name in ("pct_poverty", "pct_65_plus", "pct_limited_english"):
        out["cv_" + name.replace("pct_", "")] = cvs[name]

    out["median_income"] = (
        grouped["_income_num"] / grouped["_income_den"]
    ).where(grouped["_income_den"] > 0)

    # Everything downstream — the index, the rankings, the scatter — reads this
    # flag. The map does not.
    out["has_residents"] = out["population"] >= MIN_POPULATION

    # --- Attach geometry ---------------------------------------------------
    gdf = nhoods.merge(out, on="nhood", how="left", validate="one_to_one")

    # --- Gates -------------------------------------------------------------
    assert len(gdf) == 41, f"expected 41 neighborhoods, got {len(gdf)}"
    assert gdf.crs.to_epsg() == ANALYSIS_CRS, f"wrong CRS: {gdf.crs}"
    assert gdf.geometry.is_valid.all(), "invalid geometry survived make_valid()"
    unmatched = gdf[gdf["population"].isna()]["nhood"].tolist()
    assert not unmatched, f"neighborhoods with no ACS data: {unmatched}"
    assert int(tracts.shape[0]) == 241, f"expected 241 tracts, got {tracts.shape[0]}"

    # Nulls are tolerated only in the flagged park neighborhoods. Anywhere else
    # they mean a broken join, and the earlier version of this gate missed them
    # because it only checked population.
    scored = gdf[gdf["has_residents"]]
    null_counts = scored[list(RATES) + ["median_income"]].isna().sum()
    offenders = null_counts[null_counts > 0]
    assert offenders.empty, f"nulls in scored neighborhoods:\n{offenders}"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(OUT_PATH)

    # --- Report ------------------------------------------------------------
    print(f"\n  {len(gdf)} neighborhoods, {tracts.shape[0]} tracts, "
          f"population {int(gdf['population'].sum()):,}")
    print("  (San Francisco is roughly 810,000 — sanity check)")

    parks = gdf.loc[~gdf["has_residents"], ["nhood", "population"]]
    print(f"\n  flagged as non-residential (population < {MIN_POPULATION}), "
          f"excluded from scoring but kept on the map:")
    for _, row in parks.iterrows():
        print(f"    {row['nhood']:<20} {int(row['population']):>6,}")
    print(f"  {int(gdf['has_residents'].sum())} neighborhoods will be scored.\n")

    show = ["nhood", "population", "median_income", "pct_poverty", "pct_65_plus", "pct_65_plus"]
    ranked = scored[show].sort_values("pct_poverty", ascending=False)
    pd.set_option("display.width", 120)
    print("Highest poverty:")
    print(ranked.head(5).to_string(index=False, float_format=lambda v: f"{v:,.1f}"))
    print("\nLowest poverty:")
    print(ranked.tail(5).to_string(index=False, float_format=lambda v: f"{v:,.1f}"))

    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
