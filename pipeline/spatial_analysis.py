"""Spatial econometrics at census-tract level.

The dashboard reports 38 neighborhoods, which is too few to resolve anything
below |r| = 0.32. This runs the same variables over 241 tracts, where the floor
drops to 0.13 — and, more importantly, does it with models that account for the
fact that neighbouring tracts are not independent observations.

Two things ordinary least squares gets wrong on spatial data:

1. Standard errors come out too small, so every p-value is overconfident.
2. A spatially structured omitted variable hides in the residuals and looks like
   a relationship between whatever else is in the model.

Both matter here, because the whole claim is that fog — which is intensely
spatially structured — is the thing actually doing the work.

    python pipeline/spatial_analysis.py

Writes data/spatial_results.json and prints the findings.
"""

from __future__ import annotations

import io
import json
import math
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boundaries_acs import (  # noqa: E402
    HOUSEHOLDS, MEDIAN_INCOME, POPULATION, RATES, fetch,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "spatial_results.json"

ANALYSIS_CRS = 3310
TRACTS_URL = "https://data.sfgov.org/resource/sevw-6tgi.geojson?$limit=1000"
EXCLUDE_NEIGHBORHOOD = "The Farallones"

RASTERS = {
    "lst_anomaly": "heat",
    "fog_frequency": "fog",
    "ndvi_median": "ndvi",
    "ndbi_median": "ndbi",
    "elevation": "elevation",
    "slope": "slope",
    "southness": "southness",
    "clear_obs_count": "clear_obs",
    "sampling_bias": "heat_bias",
}

# Each block adds a mechanism. Tracking residual spatial clustering as they go
# in shows which one was actually holding the missing structure.
SPECS = {
    "demographics only": ["income_10k", "pct_poverty", "pct_poc"],
    "+ canopy": ["income_10k", "pct_poverty", "pct_poc", "ndvi"],
    "+ fog": ["income_10k", "pct_poverty", "pct_poc", "ndvi", "fog"],
    "+ terrain": ["income_10k", "pct_poverty", "pct_poc", "ndvi", "fog",
                  "elevation", "slope", "southness"],
    "+ built-up": ["income_10k", "pct_poverty", "pct_poc", "ndvi", "fog",
                   "elevation", "slope", "southness", "ndbi"],
}

# A tract needs enough measured pixels for its mean to mean anything.
MIN_PIXELS = 20
MIN_POPULATION = 200


def fisher_ci(r: float, n: int) -> tuple[float, float]:
    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
    return math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se)


def load_tracts() -> gpd.GeoDataFrame:
    blob = fetch(TRACTS_URL, "tracts.geojson")
    gdf = gpd.read_file(io.BytesIO(blob))
    gdf = gdf.rename(columns={"neighborhoods_analysis_boundaries": "nhood"})
    gdf = gdf[["geoid", "nhood", "geometry"]].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    gdf = gdf[gdf["nhood"] != EXCLUDE_NEIGHBORHOOD]
    return gdf.to_crs(ANALYSIS_CRS)


def tract_acs() -> pd.DataFrame:
    """Tract-level rates, using the same column definitions as the main pipeline."""
    from boundaries_acs import load_acs

    acs, release = load_acs()
    print(f"  ACS release: {release}")

    numeric = {}
    needed = {POPULATION, HOUSEHOLDS, MEDIAN_INCOME}
    for nums, den in RATES.values():
        needed.update(nums)
        needed.add(den)
    for col in needed:
        numeric[col] = pd.to_numeric(acs.get(col), errors="coerce")

    out = pd.DataFrame({"geoid": acs["geoid"]})
    out["population"] = numeric[POPULATION].fillna(0)
    out["households"] = numeric[HOUSEHOLDS].fillna(0)
    out["median_income"] = numeric[MEDIAN_INCOME]
    for name, (nums, den) in RATES.items():
        numerator = sum(numeric[c].fillna(0) for c in nums)
        denominator = numeric[den]
        out[name] = (100 * numerator / denominator).where(denominator > 0)
    out["pct_poc"] = 100 - out["pct_white_nh"]
    return out


def zonal(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    with rasterio.open(DATA / "lst_anomaly.tif") as src:
        transform, width, height, crs = src.transform, src.width, src.height, src.crs
    aligned = gdf.to_crs(crs)
    labels = rasterize(
        [(geom, i + 1) for i, geom in enumerate(aligned.geometry)],
        out_shape=(height, width), transform=transform, fill=0, dtype="int32",
    )
    stacks = {}
    for stem, column in RASTERS.items():
        with rasterio.open(DATA / f"{stem}.tif") as src:
            stacks[column] = src.read(1)

    rows = {c: [] for c in RASTERS.values()}
    rows["pixels"] = []
    for i in range(len(aligned)):
        mask = labels == i + 1
        for column, array in stacks.items():
            values = array[mask]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                rows[column].append(np.nanmean(values))
        heat = stacks["heat"][mask]
        rows["pixels"].append(int(np.isfinite(heat).sum()))
    return pd.DataFrame(rows)


def build() -> gpd.GeoDataFrame:
    print("Building the tract-level dataset...")
    tracts = load_tracts()
    print(f"  {len(tracts)} tracts after dropping {EXCLUDE_NEIGHBORHOOD}")
    stats = zonal(tracts)
    tracts = pd.concat([tracts.reset_index(drop=True), stats], axis=1)
    tracts = tracts.merge(tract_acs(), on="geoid", how="left")

    # Model income in $10k units. In raw dollars the coefficient is ~1e-6 and
    # prints as +0.0000, which reads as "no effect" when it is only a unit
    # choice. NDVI and fog are already 0-1, so their coefficients are per whole
    # unit — divide by ten for a per-0.1 reading.
    tracts["income_10k"] = tracts["median_income"] / 10_000

    before = len(tracts)
    keep = (
        (tracts["pixels"] >= MIN_PIXELS)
        & (tracts["population"] >= MIN_POPULATION)
        & tracts["heat"].notna()
        & tracts["median_income"].notna()
        & tracts["ndvi"].notna()
        & tracts["elevation"].notna()
        & tracts["ndbi"].notna()
    )
    tracts = tracts[keep].reset_index(drop=True)
    print(f"  {len(tracts)} usable ({before - len(tracts)} dropped: too few pixels, "
          f"too few residents, or missing income)")
    return tracts


def main() -> None:
    from esda.moran import Moran, Moran_Local
    from libpysal.weights import Queen, KNN
    from spreg import OLS, GM_Lag, GM_Error

    tracts = build()
    n = len(tracts)
    results: dict = {"n_tracts": n}

    # --- Correlations, now with enough n to mean something ----------------
    print(f"\n{'=' * 70}\n  CORRELATIONS AT n = {n}\n{'=' * 70}")
    print(f"{'relationship':<30} {'r':>7}   95% CI              vs n=38")
    pairs = [
        ("heat", "median_income"), ("heat", "pct_poc"), ("heat", "pct_poverty"),
        ("heat", "fog"), ("heat", "ndvi"),
        ("ndvi", "median_income"), ("ndvi", "pct_poc"),
    ]
    results["correlations"] = {}
    for a, b in pairs:
        sub = tracts[[a, b]].dropna()
        r = sub[a].corr(sub[b])
        lo, hi = fisher_ci(r, len(sub))
        verdict = "resolved" if lo * hi > 0 else "still includes zero"
        print(f"{a + ' vs ' + b:<30} {r:>+7.2f}   [{lo:+.2f}, {hi:+.2f}]   {verdict}")
        results["correlations"][f"{a}~{b}"] = {"r": r, "ci": [lo, hi], "n": len(sub)}

    # --- Spatial weights ---------------------------------------------------
    print(f"\n{'=' * 70}\n  SPATIAL STRUCTURE\n{'=' * 70}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = Queen.from_dataframe(tracts, use_index=False)
    if w.islands:
        # Treasure Island and similar have no land neighbours. Contiguity leaves
        # them with an empty row, which breaks the weights matrix, so they fall
        # back to nearest neighbours.
        print(f"  {len(w.islands)} island tract(s) — falling back to k=6 nearest neighbours")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            w = KNN.from_dataframe(tracts, k=6)
    w.transform = "r"
    print(f"  weights: {w.n} units, average {w.mean_neighbors:.1f} neighbours")

    print("\n  Moran's I (is each variable spatially clustered?)")
    results["morans_i"] = {}
    for col in ["heat", "fog", "ndvi", "median_income", "pct_poc"]:
        y = tracts[col].to_numpy(float)
        mi = Moran(y, w, permutations=999)
        print(f"    {col:<16} I = {mi.I:+.3f}   p = {mi.p_sim:.4f}")
        results["morans_i"][col] = {"I": mi.I, "p": mi.p_sim}

    # --- The central test --------------------------------------------------
    # If fog is the spatially structured variable actually driving heat, then a
    # model without it should leave strongly autocorrelated residuals, and
    # adding it should largely dissolve them.
    print(f"\n{'=' * 70}\n  DOES FOG EXPLAIN THE SPATIAL STRUCTURE?\n{'=' * 70}")
    y = tracts[["heat"]].to_numpy(float)

    results["models"] = {}
    print(f"  {'model':<20} {'R2':>6} {'resid I':>9} {'p':>8}")
    for label, cols in SPECS.items():
        X = tracts[cols].to_numpy(float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = OLS(y, X, w=w, spat_diag=True, moran=True,
                        name_y="heat", name_x=cols, name_ds="tracts")
        resid_i, resid_p = float(model.moran_res[0]), float(model.moran_res[2])
        print(f"  {label:<20} {model.r2:>6.3f} {resid_i:>+9.3f} {resid_p:>8.4f}")
        results["models"][label] = {
            "r2": model.r2, "resid_moran_I": resid_i, "resid_moran_p": resid_p,
            "betas": {k: float(v) for k, v in zip(model.name_x[1:], model.betas[1:, 0])},
            "pvalues": {k: float(v[1]) for k, v in zip(model.name_x[1:], model.t_stat[1:])},
            "lm_lag_p": float(model.lm_lag[1]), "lm_error_p": float(model.lm_error[1]),
        }

    full = SPECS["+ built-up"]
    print(f"\n  Coefficients, full OLS specification:")
    m = results["models"]["+ built-up"]
    for name in full:
        b, p = m["betas"][name], m["pvalues"][name]
        stars = "***" if p < .001 else "**" if p < .01 else "*" if p < .05 else ""
        print(f"    {name:<16} b = {b:+9.4f}   p = {p:.4f} {stars}")

    # Multicollinearity: NDVI and NDBI measure opposite sides of the same
    # surface, so they will be correlated. If a variance inflation factor is
    # large the coefficients are unstable and should not be read as effects.
    print("\n  Variance inflation factors (over 10 is a problem):")
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    Xf = np.c_[np.ones(n), tracts[full].to_numpy(float)]
    vifs = {}
    for i, name in enumerate(full, start=1):
        v = variance_inflation_factor(Xf, i)
        vifs[name] = float(v)
        flag = "  <- collinear" if v > 10 else ""
        print(f"    {name:<16} {v:6.2f}{flag}")
    results["vif"] = vifs

    first = results["models"]["demographics only"]["resid_moran_I"]
    last = results["models"]["+ built-up"]["resid_moran_I"]
    print(f"\n  Residual clustering across the whole sequence: "
          f"{first:+.3f} -> {last:+.3f} ({(first - last) / first * 100:.0f}% absorbed)")
    results["residual_moran_drop_pct"] = (first - last) / first * 100

    # --- A properly specified spatial model --------------------------------
    print(f"\n{'=' * 70}\n  SPATIAL MODEL\n{'=' * 70}")
    cols = SPECS["+ built-up"]
    X = tracts[cols].to_numpy(float)
    lm_lag_p = results["models"]["+ built-up"]["lm_lag_p"]
    lm_err_p = results["models"]["+ built-up"]["lm_error_p"]
    choice = "lag" if lm_lag_p < lm_err_p else "error"
    print(f"  LM diagnostics favour the spatial {choice} model.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spatial = (GM_Lag if choice == "lag" else GM_Error)(
            y, X, w=w, name_y="heat", name_x=cols, name_ds="tracts")
    print(f"  pseudo R2 = {spatial.pr2:.3f}")
    results["spatial_model"] = {"type": choice, "pr2": float(spatial.pr2), "betas": {}}
    for name, beta in zip(spatial.name_x[1:], spatial.betas[1:, 0]):
        print(f"    {name:<16} b = {float(beta):+8.4f}")
        results["spatial_model"]["betas"][name] = float(beta)

    # --- LISA --------------------------------------------------------------
    print(f"\n{'=' * 70}\n  HEAT CLUSTERS (LISA, FDR-corrected)\n{'=' * 70}")
    lisa = Moran_Local(tracts["heat"].to_numpy(float), w, permutations=999)
    # Benjamini-Hochberg across 200+ simultaneous tests, or a twentieth of them
    # are "significant" by construction.
    p = lisa.p_sim
    order = np.argsort(p)
    thresholds = 0.05 * (np.arange(1, len(p) + 1) / len(p))
    passing = p[order] <= thresholds
    cutoff = p[order][passing].max() if passing.any() else 0
    sig = p <= cutoff
    labels = {1: "hot spot", 2: "cool outlier", 3: "cool spot", 4: "hot outlier"}
    tracts["lisa"] = ["not significant"] * n
    for code, label in labels.items():
        tracts.loc[sig & (lisa.q == code), "lisa"] = label
    counts = tracts["lisa"].value_counts()
    for label in ["hot spot", "cool spot", "hot outlier", "cool outlier", "not significant"]:
        print(f"    {label:<18} {counts.get(label, 0):>4}")
    results["lisa_counts"] = {k: int(v) for k, v in counts.items()}
    results["lisa_fdr_cutoff"] = float(cutoff)

    top = tracts[tracts["lisa"] == "hot spot"]["nhood"].value_counts().head(5)
    print("\n  Neighborhoods containing the most hot-spot tracts:")
    for name, count in top.items():
        print(f"    {name:<28} {count}")

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT.name}")


if __name__ == "__main__":
    main()
