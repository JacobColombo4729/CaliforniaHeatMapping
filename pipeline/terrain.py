"""Elevation, slope and solar aspect, on the same grid as everything else.

The spatial model leaves strongly autocorrelated residuals even after fog, which
means something else spatially organised is missing. Topography is the obvious
candidate in a city built on hills: elevation sets a lapse rate and decides what
the marine layer can climb over, slope and aspect decide how much sun a surface
actually receives.

Run after landsat.py — it borrows that grid so the rasters align exactly.

    python pipeline/terrain.py

Writes elevation.tif, slope.tif and southness.tif alongside the others.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.vrt import WarpedVRT

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TEMPLATE = DATA / "lst_anomaly.tif"
PREVIEW = DATA / "terrain_preview.png"

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SF_BBOX = (-122.52, 37.70, -122.35, 37.84)

# USGS 3DEP seamless, 1/3 arc-second (~10 m), resampled to the project's 30 m
# grid. Higher resolution than the analysis needs, which is the right direction
# to err — averaging down is honest, interpolating up is not.
COLLECTION = "3dep-seamless"


def fetch_dem(transform, width, height, crs) -> np.ndarray:
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    search = catalog.search(collections=[COLLECTION], bbox=SF_BBOX)
    items = list(search.items())
    if not items:
        raise SystemExit("No 3DEP tiles returned for San Francisco.")

    # Prefer the finest resolution on offer.
    items.sort(key=lambda i: i.properties.get("gsd", 999))
    print(f"  {len(items)} DEM tile(s); using gsd = {items[0].properties.get('gsd')}")

    vrt_options = {"crs": crs, "transform": transform, "width": width, "height": height,
                   "resampling": rasterio.enums.Resampling.bilinear}
    stack = []
    for item in items:
        href = item.assets["data"].href
        with rasterio.open(href) as src:
            with WarpedVRT(src, **vrt_options) as vrt:
                band = vrt.read(1, masked=True).filled(np.nan)
        stack.append(band)
    # Tiles overlap at their edges; take whichever tile actually covered a pixel.
    return np.nanmean(np.stack(stack), axis=0)


def slope_aspect(dem: np.ndarray, pixel_m: float):
    """Horn's method — the standard 3x3 gradient used by GDAL and ArcGIS."""
    dz_dx = np.gradient(dem, pixel_m, axis=1)
    dz_dy = np.gradient(dem, pixel_m, axis=0)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))

    # Aspect measured clockwise from north. Rows increase southward, so dz_dy
    # is negated to put the gradient in map orientation rather than array
    # orientation — getting this backwards silently flips north and south.
    aspect = np.degrees(np.arctan2(-dz_dx, dz_dy)) % 360

    # "Southness": +1 facing due south, -1 facing due north. In the northern
    # hemisphere this is the axis that decides how much sun a slope receives,
    # and unlike raw aspect it is continuous — 359 degrees and 1 degree are
    # neighbours on a compass but opposite ends of a number line.
    southness = -np.cos(np.radians(aspect))
    southness[slope < 2] = 0  # flat ground faces nowhere in particular
    return slope, southness


def write(path: Path, array, profile) -> None:
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)
    print(f"  wrote {path.name}")


def main() -> None:
    if not TEMPLATE.exists():
        raise SystemExit("Run landsat.py first — this borrows its grid.")

    with rasterio.open(TEMPLATE) as src:
        transform, width, height, crs = src.transform, src.width, src.height, src.crs
        profile = src.profile.copy()
    pixel_m = abs(transform.a)
    print(f"Grid: {width} x {height} @ {pixel_m:.0f} m, {crs}")

    print("Fetching 3DEP elevation...")
    dem = fetch_dem(transform, width, height, crs)
    print(f"  elevation {np.nanmin(dem):.0f} to {np.nanmax(dem):.0f} m")

    slope, southness = slope_aspect(dem, pixel_m)
    print(f"  slope 0 to {np.nanmax(slope):.0f} degrees, "
          f"mean {np.nanmean(slope):.1f}")

    # Sanity check against geography anyone in San Francisco would know.
    from rasterio.warp import transform as warp_points
    checks = {"Twin Peaks": (-122.4477, 37.7544), "Mission (flat)": (-122.4180, 37.7600),
              "Ocean Beach": (-122.5090, 37.7590)}
    print("\n  Elevation check:")
    for name, (lon, lat) in checks.items():
        (x,), (y,) = warp_points("EPSG:4326", crs, [lon], [lat])
        col = int((x - transform.c) / pixel_m)
        row = int((transform.f - y) / pixel_m)
        value = dem[row, col] if 0 <= row < height and 0 <= col < width else np.nan
        print(f"    {name:<16} {value:6.0f} m")
    print("    (Twin Peaks is ~280 m, the Mission ~20 m, Ocean Beach ~5 m)")

    profile.update(dtype="float32", count=1, nodata=np.nan, compress="deflate")
    print()
    write(DATA / "elevation.tif", dem, profile)
    write(DATA / "slope.tif", slope, profile)
    write(DATA / "southness.tif", southness, profile)

    fig, axes = plt.subplots(1, 3, figsize=(16, 6))
    for ax, data, title, cmap in (
        (axes[0], dem, "Elevation (m)", "terrain"),
        (axes[1], slope, "Slope (degrees)", "magma"),
        (axes[2], southness, "Southness (+1 = faces south)", "RdBu_r"),
    ):
        img = ax.imshow(data, cmap=cmap)
        ax.set_title(title)
        ax.axis("off")
        fig.colorbar(img, ax=ax, shrink=0.7)
    fig.tight_layout()
    fig.savefig(PREVIEW, dpi=140)
    print(f"  wrote {PREVIEW.name}")


if __name__ == "__main__":
    main()
