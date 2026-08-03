"""Does tree canopy carry the effect of income and race onto heat?

The correlational finding is that demographics barely predict heat directly
(R2 = 0.04) while canopy predicts it strongly, and canopy itself is unevenly
distributed. That is the shape of a mediated pathway:

    income / race  --a-->  canopy  --b-->  heat
           \______________ c' _____________/

Total effect c = direct effect c' + indirect effect a*b.

Two things this script takes seriously:

1. **The bootstrap must respect spatial dependence.** Neighbouring tracts are
   not independent, so resampling them individually would produce confidence
   intervals that are far too narrow. Blocks of tracts are resampled instead,
   which preserves the local correlation structure. Both are reported so the
   difference is visible rather than assumed.

2. **A near-zero total effect does not mean no mediation.** If a*b and c' have
   opposite signs they cancel, and the raw correlation looks like nothing while
   two real opposing pathways are at work. That is inconsistent mediation, and
   it is the specific thing to look for here.

    python pipeline/mediation.py
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
OUT = ROOT / "data" / "mediation_results.json"

MEDIATOR = "ndvi"
OUTCOME = "heat"
TREATMENTS = {
    "income_10k": "median household income ($10k)",
    "pct_poc": "residents of color (%)",
}
# Two control sets, because one choice here is genuinely arguable and it changes
# the answer.
#
# NDBI (built-up surface) and NDVI (vegetation) measure opposite sides of the
# same ground and correlate strongly. Including NDBI while NDVI is the mediator
# risks over-controlling: it can absorb the very variation the mediator is
# supposed to carry, killing the indirect path by construction rather than by
# evidence. Excluding it risks the opposite — attributing to canopy what is
# really pavement.
#
# Neither is obviously right, so both are run and reported.
CONTROL_SETS = {
    "geography only": ["fog", "elevation", "slope"],
    "geography + built-up": ["fog", "elevation", "slope", "ndbi"],
}

N_BOOT = 2000
N_BLOCKS = 20
SEED = 42


def ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Coefficients only, with an intercept prepended. lstsq keeps this honest
    on the rank-deficient resamples a block bootstrap occasionally produces."""
    A = np.c_[np.ones(len(X)), X]
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return beta


def paths(df: pd.DataFrame, treatment: str, control_cols: list[str]) -> dict[str, float]:
    controls = df[control_cols].to_numpy(float)
    x = df[[treatment]].to_numpy(float)
    m = df[MEDIATOR].to_numpy(float)
    y = df[OUTCOME].to_numpy(float)

    # a: treatment -> mediator, net of controls
    a = ols(m, np.c_[x, controls])[1]
    # b and c': mediator and treatment together -> outcome
    beta = ols(y, np.c_[m, x, controls])
    b, c_direct = beta[1], beta[2]
    # c: total effect, mediator omitted
    c_total = ols(y, np.c_[x, controls])[1]

    return {"a": a, "b": b, "c_total": c_total, "c_direct": c_direct,
            "indirect": a * b}


def spatial_blocks(df: pd.DataFrame, cols: int = 5, rows: int = 4) -> np.ndarray:
    """A regular grid over the city, so the bootstrap resamples chunks of
    geography rather than scattered individual tracts.

    A grid rather than k-means: blocks come out contiguous by construction, the
    result is deterministic, and it avoids sklearn, whose threading layer faults
    on this machine.
    """
    centroids = df.geometry.centroid
    x, y = centroids.x.to_numpy(), centroids.y.to_numpy()
    # Rank-based cuts keep roughly equal counts per block despite the city's
    # irregular shape; a plain equal-width grid leaves several blocks empty.
    xi = pd.qcut(x, cols, labels=False, duplicates="drop")
    yi = pd.qcut(y, rows, labels=False, duplicates="drop")
    return (yi * cols + xi).astype(int)


def bootstrap(df: pd.DataFrame, treatment: str, control_cols: list[str],
              blocks: np.ndarray | None, n: int, seed: int) -> dict[str, tuple[float, float]]:
    rng = np.random.default_rng(seed)
    keys = ["a", "b", "c_total", "c_direct", "indirect"]
    draws: dict[str, list[float]] = {k: [] for k in keys}

    if blocks is None:
        index = np.arange(len(df))
        for _ in range(n):
            pick = rng.choice(index, size=len(index), replace=True)
            try:
                p = paths(df.iloc[pick], treatment, control_cols)
            except np.linalg.LinAlgError:
                continue
            for k in keys:
                draws[k].append(p[k])
    else:
        unique = np.unique(blocks)
        lookup = {b: np.flatnonzero(blocks == b) for b in unique}
        for _ in range(n):
            chosen = rng.choice(unique, size=len(unique), replace=True)
            pick = np.concatenate([lookup[b] for b in chosen])
            try:
                p = paths(df.iloc[pick], treatment, control_cols)
            except np.linalg.LinAlgError:
                continue
            for k in keys:
                draws[k].append(p[k])

    return {k: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
            for k, v in draws.items()}


def main() -> None:
    tracts = build()
    n = len(tracts)
    blocks = spatial_blocks(tracts)
    sizes = np.bincount(blocks)
    sizes = sizes[sizes > 0]
    print(f"\n  {n} tracts in {len(sizes)} spatial blocks "
          f"({sizes.min()}-{sizes.max()} tracts each)")
    print(f"  mediator: {MEDIATOR}   outcome: {OUTCOME}")

    results: dict = {"n": n, "n_boot": N_BOOT, "mediator": MEDIATOR,
                     "control_sets": CONTROL_SETS, "treatments": {}}

    for treatment, label in TREATMENTS.items():
        print(f"\n{'=' * 76}\n  {label.upper()}\n{'=' * 76}")
        results["treatments"][treatment] = {"label": label, "specs": {}}

        for spec, control_cols in CONTROL_SETS.items():
            print(f"\n  controls: {spec}  ({', '.join(control_cols)})")
            point = paths(tracts, treatment, control_cols)
            naive = bootstrap(tracts, treatment, control_cols, None, N_BOOT, SEED)
            spatial = bootstrap(tracts, treatment, control_cols, blocks, N_BOOT, SEED)

            rows = [
                ("a   treatment -> canopy", "a"),
                ("b   canopy -> heat", "b"),
                ("c   total effect", "c_total"),
                ("c'  direct effect", "c_direct"),
                ("a*b indirect (mediated)", "indirect"),
            ]
            print(f"    {'path':<28}{'estimate':>10}   {'naive 95% CI':<22}"
                  f"spatial-block 95% CI")
            for name, key in rows:
                lo_n, hi_n = naive[key]
                lo_s, hi_s = spatial[key]
                sig = "" if lo_s * hi_s > 0 else "   n.s."
                print(f"    {name:<28}{point[key]:>+10.4f}   "
                      f"[{lo_n:+.4f}, {hi_n:+.4f}]   [{lo_s:+.4f}, {hi_s:+.4f}]{sig}")

            ind, direct = point["indirect"], point["c_direct"]
            lo_s, hi_s = spatial["indirect"]
            mediated = lo_s * hi_s > 0
            opposing = ind * direct < 0
            if mediated and opposing:
                verdict = ("inconsistent mediation — a real mediated path cancelled by an "
                           "opposing direct one")
            elif mediated:
                verdict = "mediated path present"
            else:
                verdict = "no mediated path distinguishable from zero"
            print(f"    -> {verdict}")

            results["treatments"][treatment]["specs"][spec] = {
                "controls": control_cols, "point": point,
                "ci_naive": naive, "ci_spatial_block": spatial,
                "indirect_significant": bool(mediated),
                "opposing_signs": bool(opposing), "verdict": verdict,
            }

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT.name}")


if __name__ == "__main__":
    main()
