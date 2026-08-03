# SF Heat & Equity

**San Francisco's heat map is drawn by fog, not by income.**

In most American cities surface temperature tracks poverty closely enough that heat maps and
income maps are near-substitutes. San Francisco breaks that pattern. Across its 41 analysis
neighborhoods, summer land surface temperature correlates with median household income at
**r = −0.11** — nothing. The marine layer overrules everything else, and it is handed out by
geography rather than by wealth.

But the shade people *build* is another story, and that is where the inequity lives.

🔗 **[Live dashboard](https://JacobColombo4729.github.io/CaliforniaHeatMapping/)**

[![Surface temperature anomaly across San Francisco's 41 analysis neighborhoods, with hatching marking where the satellite has fewest usable observations](docs/dashboard.png)](https://JacobColombo4729.github.io/CaliforniaHeatMapping/)

*Cool on the ocean side, hot along the bay. Hatched neighborhoods are the ones the satellite
sees least — and they are the foggy ones, which is the central caveat of the whole project.*

---

## The finding

Computed over the 38 scored neighborhoods (three of the 41 are parks — see *Limitations*).

| Relationship | Pearson r | Spearman ρ |
|---|---|---|
| heat vs **fog** | **−0.79** | |
| heat vs **vegetation** | **−0.81** | |
| heat vs median income | −0.11 | −0.19 |
| heat vs % residents of color | +0.13 | +0.18 |
| fog vs median income | +0.15 | +0.17 |
| fog vs % residents of color | −0.03 | −0.05 |
| **vegetation vs median income** | **+0.40** | **+0.50** |
| **vegetation vs % residents of color** | **−0.37** | **−0.52** |
| **vulnerability index vs % residents of color** | **+0.53** | |

Three things follow.

**Fog and vegetation govern surface heat almost entirely**, at −0.79 and −0.81. Nothing else
comes close.

**Fog is equity-blind.** Its correlation with income is +0.15 and with race −0.03. It is a
physical accident of the coastline, and it is strong enough to flatten the heat-poverty
relationship that appears almost everywhere else. That is why the heat-versus-income scatter
on the dashboard is a flat cloud.

**Vegetation is not.** Greener neighborhoods are richer and whiter. The fog gradient is large
enough to bury this in raw temperature, but canopy is the part of the picture a city can
actually change — and it is distributed along income and racial lines.

The composite vulnerability index, which asks who can *cope* with heat rather than only who is
hot, ranks Chinatown, the Tenderloin, Japantown, South of Market and the Western Addition at
the top, and correlates +0.53 with the share of residents of color.

> Lakeshore ranks 37th of 38 despite 24.8% poverty, because it is among the foggiest and
> greenest places in the city. Exposure and disadvantage genuinely come apart here.

---

## How it works

Three scripts, run in order. Everything upstream exists to produce one file,
`data/neighborhoods.json`; the dashboard exists to display it. Nothing else crosses that line.

```
Landsat  ┐
ACS      ├──►  neighborhoods.json  ──►  docs/index.html
Canopy   ┘     41 features                one page, no dependencies
```

| Script | What it does |
|---|---|
| `pipeline/smoke_test.py` | Standalone check: reads one Landsat scene and plots it. Not part of the run. |
| `pipeline/boundaries_acs.py` | 41 neighborhood polygons + census demographics, aggregated from 241 tracts |
| `pipeline/landsat.py` | 63 summer scenes → surface temperature, fog frequency, NDVI, clear-observation counts |
| `pipeline/join_export.py` | Zonal statistics, the vulnerability index, and the exported artifact |

![Surface temperature, fog frequency and clear-sky observation counts across San Francisco](data/landsat_preview.png)

*The three rasters the pipeline produces. Note the third panel: the west side yields roughly
half the usable observations of the east, which is the clear-sky bias made visible.*

### The heat composite

One STAC query serves two products, which is why it deliberately does **not** filter on cloud
cover: clear pixels build the temperature composite, and the cloud masks discarded to make it
*are* the fog raster. A scene useless for temperature is a data point for fog.

Each scene is warped onto one canonical 30 m grid, normalized to its own citywide land mean,
and the stack reduced by median — so a regionally hot day cannot masquerade as a hot
neighborhood. Temperature is reported as an anomaly in °C against the city average.

### The index

Percentile-rank each variable across the scored neighborhoods, average within each component,
then combine the three components by **geometric mean** — multiplicative, so strength in one
cannot fully cancel exposure in another.

- **Exposure** — heat anomaly, vegetation (inverted), fog (inverted)
- **Sensitivity** — % over 65, % under 5, % below poverty
- **Capacity gap** — median income (inverted), % renters, % limited-English households

**Race and ethnicity are deliberately excluded from the index.** Had they been inputs, the
finding that vulnerability falls hardest on communities of color would be circular. Kept out,
it is a result.

---

## Data sources

| Layer | Source |
|---|---|
| Surface temperature, fog, NDVI | Landsat Collection 2 Level-2 via [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/) |
| Demographics | American Community Survey 2024 5-year via [Census Reporter](https://censusreporter.org/) |
| Neighborhood boundaries | [DataSF `j2bu-swwd`](https://data.sfgov.org/-/Analysis-Neighborhoods/j2bu-swwd) |
| Tract → neighborhood crosswalk | [DataSF `sevw-6tgi`](https://data.sfgov.org/Geographic-Locations-and-Boundaries/Analysis-Neighborhoods-2020-census-tracts-assigned/sevw-6tgi/about_data) |

Demographics come from Census Reporter's API rather than the Census Bureau's own endpoint.
It requires no API key and returns every needed table in one request. It is a third-party
wrapper around ACS, not an official endpoint — swapping back is a one-function change.

---

## Limitations

**Surface temperature is not air temperature.** Landsat measures the skin temperature of roofs
and pavement, which runs far hotter than the air a person stands in. The spatial pattern is
meaningful; absolute values are not comparable to a weather station.

**Daytime only.** Landsat passes late morning. Night-time heat retention is the more
health-relevant variable and is not measured here.

**Clear-sky bias — the important one, and it is measured rather than asserted.** A thermal
sensor cannot see through fog, so the foggiest neighborhoods are observed almost exclusively on
their rare clear days, which are their warm days. Usable observations run from **14 to 41** of
63 scenes.

The pipeline quantifies the distortion directly: for every pixel it computes the average
citywide temperature on the days that pixel was visible, minus the average across all scenes.
**Every neighborhood comes out biased warm, from +0.43 °C to +1.25 °C, and the bias correlates
r = +0.58 with fog** — the foggier the place, the more its coolness is hidden. Lakeshore, the
foggiest scored neighborhood, is seen on 14 of 63 scenes with a +1.25 °C bias.

So every temperature number here understates how much cooler the west really is, which makes
the canopy inequity a floor rather than a ceiling. The dashboard hatches the affected
neighborhoods, maps the bias, and grades every neighborhood's measurement confidence.

Worth stating explicitly: **this bias does not manufacture the headline null result.** Sampling
bias correlates only weakly with income, and in the direction that would make the heat–income
relationship *more* negative if corrected — not closer to zero.

**Census estimates carry margins, and they are propagated.** Margins of error are combined in
quadrature across tracts and through the ratio formula, giving each neighborhood a coefficient
of variation. Against the Census Bureau's own thresholds, 28 of 38 scored neighborhoods are
reliable, 8 warrant caution and 2 are unreliable. Per-variable margins are in the data table.

**Three neighborhoods are parks.** Golden Gate Park (58 residents), McLaren Park (146) and
Lincoln Park (160, and zero households) produce census rates that are pure noise — McLaren Park
reads 75% poverty off ~115 households. They are mapped but never scored or ranked.

**Neighborhood median income is an approximation.** Medians cannot be summed, so it is a
household-weighted mean of tract medians. Every other rate is exact: summed numerator over
summed denominator, which is what population weighting actually means.

**41 units is coarse.** Real inequity exists within neighborhoods and is invisible here.

**The Farallon Islands are excluded.** Legally San Francisco County, 30 km offshore, and absent
from the city's own boundary file.

---

## Reproduce

```bash
conda env create -f environment.yml
conda activate sfheat
python -m pip install --force-reinstall --no-deps rasterio rioxarray pyogrio pyarrow

python pipeline/boundaries_acs.py   # ~30 s
python pipeline/landsat.py          # ~15 min first run, then cached
python pipeline/join_export.py      # ~5 s
```

See `environment.yml` for why four packages come from pip. No API keys are required.

The dashboard is a single static HTML file with no build step and no dependencies — maps and
charts are inline SVG. Open `docs/index.html` directly, or serve the folder:

```bash
python -m http.server 8765 --directory docs
```

`PROJECT_PLAN.md` carries the full decision log, including what was cut and why.
