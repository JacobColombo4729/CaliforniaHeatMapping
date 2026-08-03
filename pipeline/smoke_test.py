"""Smoke test — can we reach Landsat and read a thermal band over San Francisco?

Run this before writing anything else. It rehearses the exact pattern the real
Landsat script uses on Saturday: STAC search, sign the asset, windowed read of
just the SF footprint. If this works, Saturday is about analysis. If it fails,
fix it tonight.

    python pipeline/00_smoke_test.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import planetary_computer
import pystac_client
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

# WGS84 bounding box. Deliberately excludes the Farallon Islands, which are
# legally in SF County and 30 km offshore.
SF_BBOX = (-122.52, 37.70, -122.35, 37.84)
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Landsat Collection 2 Level-2 surface temperature scaling.
ST_SCALE = 0.00341802
ST_OFFSET = 149.0
KELVIN = 273.15

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "smoke_test.png"


def main() -> None:
    print("Searching the Planetary Computer catalog...")
    catalog = pystac_client.Client.open(
        STAC_URL, modifier=planetary_computer.sign_inplace
    )

    # Note the absence of a cloud-cover filter. On Saturday this same query
    # feeds two outputs: clear scenes build the temperature composite, all
    # scenes build fog frequency. One query, two uses.
    search = catalog.search(
        collections=["landsat-c2-l2"],
        bbox=SF_BBOX,
        datetime="2023-06-01/2023-09-30",
        query={"platform": {"in": ["landsat-8", "landsat-9"]}},
    )

    items = sorted(search.items(), key=lambda i: i.properties["eo:cloud_cover"])
    if not items:
        raise SystemExit("No scenes returned. Check the bbox and date range.")

    print(f"  {len(items)} scenes found for summer 2023")

    item = items[0]
    cloud = item.properties["eo:cloud_cover"]
    print(f"  clearest: {item.id}  ({cloud:.1f}% cloud)")

    # Read only the SF window rather than the full ~8000x8000 scene. This is
    # what keeps the real pipeline fast.
    href = item.assets["lwir11"].href
    print("Reading the thermal band over the SF window...")

    with rasterio.open(href) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *SF_BBOX)
        window = from_bounds(*bounds, transform=src.transform)
        raw = src.read(1, window=window)

    lst_c = raw * ST_SCALE + ST_OFFSET - KELVIN
    print(f"  window: {raw.shape[1]} x {raw.shape[0]} px")
    print(f"  surface temperature: {lst_c.min():.1f}C to {lst_c.max():.1f}C")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 6))
    img = ax.imshow(lst_c, cmap="magma")
    ax.set_title(f"Land surface temperature — {item.datetime:%Y-%m-%d}")
    ax.axis("off")
    fig.colorbar(img, ax=ax, label="degrees C", shrink=0.8)
    fig.tight_layout()
    fig.savefig(OUT_PATH, dpi=150)

    print(f"\nWrote {OUT_PATH}")
    print("Open it. If it looks like San Francisco, you are ready for Saturday.")


if __name__ == "__main__":
    main()
