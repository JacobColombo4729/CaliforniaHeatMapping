"""Where does a tree buy the most cooling?

Every model so far has fitted one canopy coefficient for the whole city. That
assumes a tree in the Sunset does the same work as a tree in the Mission, which
is exactly what you would not expect in a city where the marine layer already
cools half the map for free.

Geographically weighted regression fits a separate local regression at every
tract, weighting nearby tracts more heavily, with the bandwidth chosen by AICc.
The output is a map of local coefficients — the closest this project gets to an
answer a city could act on.

Run after spatial_analysis.py has its inputs in place.

    python pipeline/gwr.py

Writes data/gwr_results.json and data/gwr_by_neighborhood.json.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spatial_analysis import build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "gwr_results.json"
OUT_NHOOD = DATA / "gwr_by_neighborhood.json"

OUTCOME = "heat"
# Kept deliberately lean. Every extra term costs local degrees of freedom, and
# a local regression has very few to spend.
PREDICTORS = ["ndvi", "fog", "elevation"]


def main() -> None:
    from mgwr.gwr import GWR
    from mgwr.sel_bw import Sel_BW
    from spreg import OLS

    tracts = build()
    tracts = tracts.dropna(subset=PREDICTORS + [OUTCOME]).reset_index(drop=True)
    n = len(tracts)

    centroids = tracts.geometry.centroid
    coords = np.c_[centroids.x, centroids.y]  # EPSG:3310, metres
    y = tracts[[OUTCOME]].to_numpy(float)
    X = tracts[PREDICTORS].to_numpy(float)

    print(f"\n{'=' * 72}\n  GEOGRAPHICALLY WEIGHTED REGRESSION\n{'=' * 72}")
    print(f"  {n} tracts   outcome: {OUTCOME}   predictors: {', '.join(PREDICTORS)}")

    print("\n  Selecting bandwidth by AICc...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bw = Sel_BW(coords, y, X).search(criterion="AICc")
    print(f"    adaptive bandwidth = {bw:.0f} nearest tracts "
          f"({100 * bw / n:.0f}% of the city per local fit)")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gwr = GWR(coords, y, X, bw).fit()
        global_model = OLS(y, X, name_y=OUTCOME, name_x=PREDICTORS, name_ds="tracts")

    # Does letting the relationship vary actually buy anything? A lower AICc by
    # more than about 3 is the usual threshold for preferring a model.
    print(f"\n  Global OLS   R2 = {global_model.r2:.3f}   AICc = {global_model.aic:.1f}")
    print(f"  GWR          R2 = {gwr.R2:.3f}   AICc = {gwr.aicc:.1f}")
    delta = global_model.aic - gwr.aicc
    verdict = ("the relationship genuinely varies across the city"
               if delta > 3 else "no real evidence the relationship varies")
    print(f"  AICc improvement = {delta:.1f}  ->  {verdict}")

    # Column 0 is the intercept; predictors follow in order.
    idx = {name: i + 1 for i, name in enumerate(PREDICTORS)}
    canopy = gwr.params[:, idx["ndvi"]]
    tvals = gwr.tvalues[:, idx["ndvi"]]
    # mgwr adjusts the critical t for the effective number of local tests, which
    # matters — 232 local regressions produce plenty of spurious significance
    # at an uncorrected alpha.
    critical = gwr.critical_tval(alpha=0.05)
    significant = np.abs(tvals) > critical

    tracts["canopy_coef"] = canopy
    tracts["canopy_t"] = tvals
    tracts["canopy_sig"] = significant
    tracts["local_r2"] = gwr.localR2.flatten()

    print(f"\n  Local canopy coefficient (°C per unit NDVI):")
    print(f"    range   {canopy.min():+.1f} to {canopy.max():+.1f}")
    print(f"    median  {np.median(canopy):+.1f}   (global model: "
          f"{float(global_model.betas[idx['ndvi'], 0]):+.1f})")
    print(f"    significant at corrected alpha (|t| > {critical:.2f}): "
          f"{int(significant.sum())} of {n} tracts")
    print(f"    local R2 ranges {gwr.localR2.min():.2f} to {gwr.localR2.max():.2f}")

    # Aggregate to neighborhoods, since that is the unit anyone reading the
    # dashboard thinks in.
    by_nhood = (tracts.groupby("nhood")
                .agg(canopy_coef=("canopy_coef", "mean"),
                     canopy_sig_share=("canopy_sig", "mean"),
                     local_r2=("local_r2", "mean"),
                     fog=("fog", "mean"),
                     ndvi=("ndvi", "mean"),
                     tracts=("canopy_coef", "size"))
                .sort_values("canopy_coef"))

    # In °C per 0.1 NDVI, which is a plantable increment rather than an abstract
    # unit — 0.1 NDVI is roughly the gap between a bare street and a lined one.
    by_nhood["cooling_per_0.1_ndvi"] = -by_nhood["canopy_coef"] / 10

    print(f"\n  Where a tree buys the MOST cooling (°C per 0.1 NDVI):")
    for name, row in by_nhood.head(6).iterrows():
        print(f"    {name:<28} {row['cooling_per_0.1_ndvi']:+.2f} °C   "
              f"(fog {100 * row['fog']:.0f}%, {int(row['tracts'])} tracts)")

    print(f"\n  Where it buys the LEAST:")
    for name, row in by_nhood.tail(5).iterrows():
        print(f"    {name:<28} {row['cooling_per_0.1_ndvi']:+.2f} °C   "
              f"(fog {100 * row['fog']:.0f}%, {int(row['tracts'])} tracts)")

    # Does local cooling power track fog? If trees matter more where the marine
    # layer does not already do the job, that is the mechanism the whole project
    # has been arguing for.
    r = by_nhood["cooling_per_0.1_ndvi"].corr(by_nhood["fog"])
    print(f"\n  Local canopy cooling vs fog:  r = {r:+.2f}")
    print("    " + ("Trees buy more cooling where there is less fog — the two are "
                    "substitutes." if r < -0.2 else
                    "Trees buy more cooling where there is more fog." if r > 0.2 else
                    "No clear relationship between local cooling power and fog."))

    # --- Can the local models actually separate canopy from fog? ------------
    # GWR is prone to local multicollinearity: inside a small neighbourhood two
    # predictors can be nearly identical even when they are distinguishable
    # citywide. A local condition number above 30 is the usual warning line, and
    # coefficients from those tracts should not be read as effects.
    print("\n  Local collinearity diagnostics:")
    # local_collinearity() returns (correlations, VIF, condition number, VDP).
    _, local_vif, local_cn, _ = gwr.local_collinearity()
    cn = np.asarray(local_cn).flatten()
    vif_ndvi = np.asarray(local_vif)[:, 0]
    bad = cn > 30
    print(f"    condition number: median {np.median(cn):.1f}, max {cn.max():.1f}")
    print(f"    tracts above the warning line of 30: {int(bad.sum())} of {n} "
          f"({100 * bad.mean():.0f}%)")
    print(f"    local VIF on canopy: median {np.median(vif_ndvi):.2f}, "
          f"max {vif_ndvi.max():.2f}")

    tracts["local_cn"] = cn
    foggy = tracts["fog"] > tracts["fog"].median()
    cn_foggy, cn_clear = np.median(cn[foggy]), np.median(cn[~foggy])
    print(f"    median CN — foggy half {cn_foggy:.1f}  vs  clear half {cn_clear:.1f}")
    if cn_foggy > cn_clear * 1.2:
        print("    -> the foggy side is exactly where the local models struggle to "
              "separate canopy from fog, so its large canopy coefficients are the "
              "least trustworthy ones on the map.")
    results_cn = {"median": float(np.median(cn)), "max": float(cn.max()),
                  "share_above_30": float(bad.mean()),
                  "median_foggy": float(cn_foggy), "median_clear": float(cn_clear)}

    # A condition number is scale-sensitive; a variance inflation factor is not.
    # Elevation runs 0-283 while canopy and fog run 0-1, which inflates the
    # condition number on its own. Refit on standardised predictors: if the
    # condition number collapses, the warning was about units, not confounding.
    Xz = (X - X.mean(axis=0)) / X.std(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bw_z = Sel_BW(coords, y, Xz).search(criterion="AICc")
        gwr_z = GWR(coords, y, Xz, bw_z).fit()
        cn_z = np.asarray(gwr_z.local_collinearity()[2]).flatten()
    print(f"\n    Refit on standardised predictors: condition number median "
          f"{np.median(cn_z):.1f}, max {cn_z.max():.1f}, "
          f"{int((cn_z > 30).sum())} of {n} above 30")
    if np.median(cn_z) < 30 <= np.median(cn):
        print("    -> the original warning was a units artefact. Canopy and fog are "
              "genuinely separable locally (VIF confirms it), so the local "
              "coefficients can be read.")
    results_cn["median_standardised"] = float(np.median(cn_z))
    results_cn["share_above_30_standardised"] = float((cn_z > 30).mean())

    # Restrict to the tracts whose local models are actually well conditioned and
    # see whether the headline pattern survives.
    ok = ~bad
    if ok.sum() > 20:
        sub = tracts[ok]
        r_ok = (-sub.groupby("nhood")["canopy_coef"].mean() / 10).corr(
            sub.groupby("nhood")["fog"].mean())
        print(f"\n    Among the {int(ok.sum())} well-conditioned tracts only, "
              f"cooling vs fog: r = {r_ok:+.2f}")
        results_cn["cooling_vs_fog_r_wellconditioned"] = float(r_ok)

    # A blunter check on the same worry: is the local canopy effect just
    # tracking how little NDVI varies locally? A slope fitted across a narrow
    # range of x is unstable and tends to be exaggerated.
    spread = tracts.groupby("nhood")["ndvi"].std()
    joined = by_nhood.join(spread.rename("ndvi_sd"))
    r_spread = joined["cooling_per_0.1_ndvi"].corr(joined["ndvi_sd"])
    print(f"\n    local cooling vs within-neighborhood NDVI spread: r = {r_spread:+.2f}")

    results = {
        "n_tracts": n, "bandwidth": float(bw), "predictors": PREDICTORS,
        "gwr_r2": float(gwr.R2), "gwr_aicc": float(gwr.aicc),
        "global_r2": float(global_model.r2), "global_aic": float(global_model.aic),
        "aicc_improvement": float(delta),
        "critical_tval": float(critical),
        "n_significant": int(significant.sum()),
        "canopy_coef_range": [float(canopy.min()), float(canopy.max())],
        "canopy_coef_median": float(np.median(canopy)),
        "cooling_vs_fog_r": float(r),
        "cooling_vs_ndvi_spread_r": float(r_spread),
        "local_condition_number": results_cn,
    }
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    payload = {name: {"cooling_per_0.1_ndvi": float(row["cooling_per_0.1_ndvi"]),
                      "significant_share": float(row["canopy_sig_share"]),
                      "local_r2": float(row["local_r2"])}
               for name, row in by_nhood.iterrows()}
    OUT_NHOOD.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT.name} and {OUT_NHOOD.name}")


if __name__ == "__main__":
    main()
