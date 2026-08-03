"""Step 2 of 3 — surface temperature and fog frequency from Landsat.

One STAC query, two products. Clear pixels build the summer temperature
composite; the cloud masks that were discarded to make it become the fog
frequency raster. Getting this right is why the query does NOT filter on cloud
cover — a scene that is useless for temperature is a data point for fog.

Run after boundaries_acs.py, before join_export.py.

    python pipeline/landsat.py

The per-scene reads are cached to data/interim/scene_stack.npz. The first run
takes several minutes; later runs are instant. Delete that file to refetch.
"""

from __future__ import annotations

import math
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as warp_points
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
CACHE = INTERIM / "scene_stack.npz"
PREVIEW = ROOT / "data" / "landsat_preview.png"

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# WGS84. Deliberately excludes the Farallon Islands.
SF_BBOX = (-122.52, 37.70, -122.35, 37.84)

# UTM 10N is what Landsat already uses here, so resampling stays minimal.
TARGET_CRS = "EPSG:32610"
RESOLUTION = 30

DATE_RANGE = "2019-06-01/2024-09-30"
SUMMER_MONTHS = {6, 7, 8, 9}

# Landsat Collection 2 Level-2 surface temperature scaling.
ST_SCALE = 0.00341802
ST_OFFSET = 149.0
KELVIN = 273.15

# Surface reflectance scaling, used for NDVI.
SR_SCALE = 0.0000275
SR_OFFSET = -0.2

# QA_PIXEL bit positions.
BIT_FILL = 0
BIT_DILATED_CLOUD = 1
BIT_CIRRUS = 2
BIT_CLOUD = 3
BIT_CLOUD_SHADOW = 4
BIT_WATER = 7

# A scene covering only a sliver of the city would skew its own citywide mean,
# so it is dropped rather than normalized against a partial view.
MIN_SCENE_COVERAGE = 0.5

# A pixel needs this many clear looks before its median means anything.
MIN_CLEAR_OBS = 5

# Pixels that read as water in more than half of all valid observations are
# masked out, so bay pixels cannot cool the waterfront neighborhoods' averages.
WATER_FRACTION = 0.5

MAX_WORKERS = 8

# Fog should be high on the ocean side and low in the southeast. These are the
# check points, not decoration — if the gradient inverts, the QA bits are wrong.
FOG_CHECK_POINTS = {
    "Outer Sunset (west)": (-122.494, 37.754),
    "Richmond (west)": (-122.482, 37.780),
    "Twin Peaks (middle)": (-122.447, 37.751),
    "Mission (east)": (-122.418, 37.760),
    "Bayview (southeast)": (-122.390, 37.730),
}


def target_grid() -> tuple[object, int, int]:
    """One canonical grid every scene is warped onto, snapped to 30 m."""
    left, bottom, right, top = transform_bounds("EPSG:4326", TARGET_CRS, *SF_BBOX)
    left = math.floor(left / RESOLUTION) * RESOLUTION
    bottom = math.floor(bottom / RESOLUTION) * RESOLUTION
    right = math.ceil(right / RESOLUTION) * RESOLUTION
    top = math.ceil(top / RESOLUTION) * RESOLUTION
    width = int((right - left) / RESOLUTION)
    height = int((top - bottom) / RESOLUTION)
    return from_origin(left, top, RESOLUTION, RESOLUTION), width, height


def sf_mask(transform, width, height) -> np.ndarray:
    """Pixels inside San Francisco itself.

    The read window is a rectangle, so it also contains the Marin headlands and
    a slice of the East Bay. Those are land, and without this they would join
    the "citywide" mean each scene is normalized against — making the anomaly
    relative to somewhere that is not the city.
    """
    gdf = gpd.read_parquet(ROOT / "data" / "neighborhoods_acs.parquet").to_crs(TARGET_CRS)
    burned = rasterize(
        [(geom, 1) for geom in gdf.geometry],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
    )
    return burned.astype(bool)


def search_scenes() -> list:
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    # No cloud-cover filter. That is the whole point.
    search = catalog.search(
        collections=["landsat-c2-l2"],
        bbox=SF_BBOX,
        datetime=DATE_RANGE,
        query={"platform": {"in": ["landsat-8", "landsat-9"]}},
    )
    items = [i for i in search.items() if i.datetime.month in SUMMER_MONTHS]
    return sorted(items, key=lambda i: i.datetime)


def read_scene(item, transform, width, height):
    """Warp one scene onto the canonical grid. Returns arrays or None."""
    vrt_options = {
        "crs": TARGET_CRS,
        "transform": transform,
        "width": width,
        "height": height,
    }
    out = {}
    for key, asset in (
        ("st", "lwir11"),
        ("qa", "qa_pixel"),
        ("red", "red"),
        ("nir", "nir08"),
        ("swir", "swir16"),
    ):
        with rasterio.open(item.assets[asset].href) as src:
            with WarpedVRT(src, **vrt_options) as vrt:
                out[key] = vrt.read(1)

    qa = out["qa"].astype(np.uint16)
    raw = out["st"].astype(np.float32)

    # Fill comes from two directions: the scene's own fill flag, and the empty
    # margin left when a scene does not cover the whole window.
    filled = ((qa >> BIT_FILL) & 1).astype(bool) | (raw == 0) | (qa == 0)
    valid = ~filled

    coverage = valid.mean()
    if coverage < MIN_SCENE_COVERAGE:
        return None

    cloud = (
        ((qa >> BIT_DILATED_CLOUD) & 1)
        | ((qa >> BIT_CIRRUS) & 1)
        | ((qa >> BIT_CLOUD) & 1)
        | ((qa >> BIT_CLOUD_SHADOW) & 1)
    ).astype(bool) & valid

    water = ((qa >> BIT_WATER) & 1).astype(bool) & valid

    lst = raw * ST_SCALE + ST_OFFSET - KELVIN
    lst[~valid] = np.nan

    # NDVI stands in for tree canopy. The DataSF canopy layer is 289,219
    # polygons — too heavy for this scope — and street tree points would miss
    # every park and back yard, which is most of the city's shade.
    red = out["red"].astype(np.float32) * SR_SCALE + SR_OFFSET
    nir = out["nir"].astype(np.float32) * SR_SCALE + SR_OFFSET
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / (nir + red)
    ndvi[~valid] = np.nan
    ndvi = np.clip(ndvi, -1, 1)

    # Built-up index. NDVI says where vegetation is absent; NDBI says where
    # pavement and rooftops are present. They are related but not the same
    # thing — bare soil and dry grass read low on NDVI without being impervious —
    # and the thermal mass of the built surface is its own mechanism.
    swir = out["swir"].astype(np.float32) * SR_SCALE + SR_OFFSET
    with np.errstate(invalid="ignore", divide="ignore"):
        ndbi = (swir - nir) / (swir + nir)
    ndbi[~valid] = np.nan
    ndbi = np.clip(ndbi, -1, 1)

    return (lst.astype(np.float32), ndvi.astype(np.float32),
            ndbi.astype(np.float32), cloud, water, valid)


def build_stack(items, transform, width, height):
    if CACHE.exists():
        print(f"  cached stack: {CACHE.name}")
        z = np.load(CACHE)
        return z["lst"], z["ndvi"], z["ndbi"], z["cloud"], z["water"], z["valid"]

    lst_list, ndvi_list, ndbi_list = [], [], []
    cloud_list, water_list, valid_list = [], [], []
    skipped = 0
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(read_scene, item, transform, width, height): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as exc:
                skipped += 1
                print(f"    [{done}/{len(items)}] FAILED {item.id}: {exc}")
                continue
            if result is None:
                skipped += 1
                continue
            lst, ndvi, ndbi, cloud, water, valid = result
            lst_list.append(lst)
            ndvi_list.append(ndvi)
            ndbi_list.append(ndbi)
            cloud_list.append(cloud)
            water_list.append(water)
            valid_list.append(valid)
            if done % 10 == 0:
                print(f"    [{done}/{len(items)}] read")

    print(f"  kept {len(lst_list)} scenes, skipped {skipped}")

    lst = np.stack(lst_list)
    ndvi = np.stack(ndvi_list)
    ndbi = np.stack(ndbi_list)
    cloud = np.stack(cloud_list)
    water = np.stack(water_list)
    valid = np.stack(valid_list)

    INTERIM.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        CACHE, lst=lst, ndvi=ndvi, ndbi=ndbi, cloud=cloud, water=water, valid=valid
    )
    return lst, ndvi, ndbi, cloud, water, valid


def write_raster(path: Path, array: np.ndarray, transform, width, height) -> None:
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": TARGET_CRS,
        "transform": transform,
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)
    print(f"  wrote {path.name}")


def sample(array, transform, lon, lat):
    (x,), (y,) = warp_points("EPSG:4326", TARGET_CRS, [lon], [lat])
    col = int((x - transform.c) / RESOLUTION)
    row = int((transform.f - y) / RESOLUTION)
    if 0 <= row < array.shape[0] and 0 <= col < array.shape[1]:
        return array[row, col]
    return np.nan


def main() -> None:
    transform, width, height = target_grid()
    print(f"Target grid: {width} x {height} px @ {RESOLUTION} m, {TARGET_CRS}\n")

    print("Searching...")
    items = search_scenes()
    print(f"  {len(items)} summer scenes, {items[0].datetime:%Y-%m-%d} to "
          f"{items[-1].datetime:%Y-%m-%d}\n")

    print("Reading scenes...")
    lst, ndvi, ndbi, cloud, water, valid = build_stack(items, transform, width, height)
    n_scenes = lst.shape[0]

    # --- Water mask --------------------------------------------------------
    # The QA water bit is only set on CLEAR pixels, so the denominator must be
    # clear observations, not all valid ones. Dividing by all valid looks scores
    # the ocean at roughly 0.35 — it is fogged most of the time and therefore
    # flagged cloud, never water — which silently leaves the bay unmasked.
    valid_count = valid.sum(axis=0)
    clear_valid = valid & ~cloud
    clear_valid_count = clear_valid.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        water_frac = np.where(
            clear_valid_count > 0, water.sum(axis=0) / clear_valid_count, 0
        )
    is_water = water_frac > WATER_FRACTION
    print(f"\n  water mask: {is_water.sum():,} px "
          f"({100 * is_water.mean():.1f}% of grid)")

    # --- Fog frequency -----------------------------------------------------
    # Denominator is VALID observations, not scene count. A pixel outside a
    # scene's footprint was never observed and must not count as clear.
    with np.errstate(invalid="ignore", divide="ignore"):
        fog = np.where(valid_count > 0, cloud.sum(axis=0) / valid_count, np.nan)
    fog[is_water] = np.nan

    # --- Temperature composite --------------------------------------------
    # Normalize each scene to its own citywide land mean first, so a regionally
    # hot day cannot dominate the spatial pattern. The result is degrees C
    # relative to that day's city average, which is also the more interpretable
    # number for a dashboard.
    clear = valid & ~cloud & ~is_water[None, :, :]
    clear_count = clear.sum(axis=0)

    lst_clear = np.where(clear, lst, np.nan)

    # The baseline is San Francisco land only — not the Marin headlands, not the
    # East Bay, not the water.
    city = sf_mask(transform, width, height) & ~is_water
    print(f"  SF land in window: {city.sum():,} px "
          f"({100 * city.mean():.1f}% of grid)")
    lst_city = np.where(clear & city[None, :, :], lst, np.nan)

    # A fully clouded scene is an all-NaN slice. That is expected here, not an
    # error — it contributes nothing to the median and everything to the fog
    # raster — so the warning it raises is noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        scene_means = np.nanmean(lst_city.reshape(n_scenes, -1), axis=1)
        anomaly = lst_clear - scene_means[:, None, None]
        lst_anomaly = np.nanmedian(anomaly, axis=0)
        lst_absolute = np.nanmedian(lst_clear, axis=0)
    print(f"  {np.isnan(scene_means).sum()} scenes were fully clouded over land")

    # Vegetation, from the same clear pixels. Greenest-season median rather than
    # mean, for the same reason temperature uses one: a single hazy scene should
    # not move it.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ndvi_median = np.nanmedian(np.where(clear, ndvi, np.nan), axis=0)
        ndbi_median = np.nanmedian(np.where(clear, ndbi, np.nan), axis=0)

    # --- How biased is each pixel's sample of days? ----------------------
    # A foggy pixel is only ever seen on its rare clear days, and those are its
    # warm days. Measure that directly: the average citywide temperature on the
    # days this pixel was clear, minus the average across every scene. Positive
    # means "we only get to look at this place when the city is hot", so its
    # reading is biased warm and its true coolness is understated.
    usable = np.isfinite(scene_means)
    overall_mean = float(np.nanmean(scene_means))
    weights = clear & usable[:, None, None]
    seen_sum = (weights * np.nan_to_num(scene_means)[:, None, None]).sum(axis=0)
    seen_n = weights.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        sampling_bias = np.where(seen_n > 0, seen_sum / seen_n - overall_mean, np.nan)

    thin = clear_count < MIN_CLEAR_OBS
    lst_anomaly[thin] = np.nan
    lst_absolute[thin] = np.nan
    ndvi_median[thin] = np.nan
    ndbi_median[thin] = np.nan
    sampling_bias[thin] = np.nan
    print(f"  dropped {thin.sum():,} px with fewer than {MIN_CLEAR_OBS} clear looks")
    print(f"  anomaly range: {np.nanmin(lst_anomaly):+.1f} to "
          f"{np.nanmax(lst_anomaly):+.1f} C")
    print(f"  absolute median: {np.nanmedian(lst_absolute):.1f} C\n")

    # --- Validate the fog gradient against geography we already know -------
    print("Fog frequency check (west should be high, southeast low):")
    readings = {}
    for name, (lon, lat) in FOG_CHECK_POINTS.items():
        value = sample(fog, transform, lon, lat)
        readings[name] = value
        print(f"    {name:<22} {100 * value:5.1f}%")

    west = np.nanmean([readings["Outer Sunset (west)"], readings["Richmond (west)"]])
    east = np.nanmean([readings["Mission (east)"], readings["Bayview (southeast)"]])
    if not (west > east):
        print("\n  WARNING: fog is not higher in the west. Suspect the QA bit order.")
    else:
        print(f"\n  west {100 * west:.1f}% vs southeast {100 * east:.1f}% "
              f"— gradient is {100 * (west - east):.1f} points, as expected.")

    # --- Write -------------------------------------------------------------
    print()
    write_raster(ROOT / "data" / "lst_anomaly.tif", lst_anomaly, transform, width, height)
    write_raster(ROOT / "data" / "lst_absolute.tif", lst_absolute, transform, width, height)
    write_raster(ROOT / "data" / "ndvi_median.tif", ndvi_median, transform, width, height)
    write_raster(ROOT / "data" / "ndbi_median.tif", ndbi_median, transform, width, height)
    write_raster(ROOT / "data" / "fog_frequency.tif", fog, transform, width, height)
    write_raster(ROOT / "data" / "clear_obs_count.tif", clear_count.astype(np.float32),
                 transform, width, height)
    write_raster(ROOT / "data" / "sampling_bias.tif", sampling_bias, transform, width, height)

    # --- Eyeball it --------------------------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=(16, 6))
    for ax, data, title, cmap in (
        (axes[0], lst_anomaly, "Surface temperature anomaly (C)", "magma"),
        (axes[1], 100 * fog, "Cloud/fog frequency (%)", "Blues"),
        (axes[2], clear_count.astype(float), "Clear observations", "viridis"),
    ):
        img = ax.imshow(data, cmap=cmap)
        ax.set_title(title)
        ax.axis("off")
        fig.colorbar(img, ax=ax, shrink=0.7)
    fig.suptitle(f"San Francisco — {n_scenes} Landsat summer scenes, 2019-2024")
    fig.tight_layout()
    fig.savefig(PREVIEW, dpi=140)
    print(f"  wrote {PREVIEW.name}")
    print("\nOpen the preview. Cool west, warm southeast; fog the mirror image.")


if __name__ == "__main__":
    main()
