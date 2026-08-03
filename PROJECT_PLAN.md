# SF Heat & Equity — Project Plan

A single scrolling web page telling the story of how San Francisco's heat burden falls
unevenly across its 41 neighborhoods. Built on satellite-measured surface temperature and
census demographics. Weekend scope, portfolio deliverable.

---

**Everything upstream exists to produce `neighborhoods.json`. Everything downstream exists
to display it.**

```
Landsat  ┐
ACS      ├──►  neighborhoods.json  ──►  index.html
Canopy   ┘     41 features                one page
               ~20 properties each
```

If a piece of work doesn't either produce that file or render it, it isn't in this project.
That rule is what keeps this simple, and it's the rule the old plan didn't have.

It also buys a real option: the heat column can be recomputed from better data later without
touching a line of frontend code.

---

## Decisions

| Question | Decision | Why |
|---|---|---|
| Purpose | Portfolio piece, geospatial roles | Sets everything below |
| Map unit | 41 SF Analysis Neighborhoods | Named units. "Bayview" tells a story; "060750231021" does not |
| Heat data | Processed from Landsat myself | For geospatial roles, raster processing is the credential |
| Fog layer | Keep | Nearly free once cloud masks are in memory, and it's the differentiator |
| Race/ethnicity | Descriptive overlay, not an index input | Keeps the headline finding a result instead of a circular construction |
| Frontend | One static HTML file | No build step, no server, no framework to maintain |
| Hosting | GitHub Pages | Free, permanent, linkable |
| Time | One weekend, ~15h | Scope is cut to fit this, not the other way round |

---

## Cut from the previous plan

Named explicitly so they stay cut:

PostGIS · DuckDB spatial SQL · ECOSTRESS · CAPA traverse data · NAIP downscaling · Sky View
Factor · OSMnx isochrones and cooling-center access · hex grid and MAUP check · Moran's I ·
LISA cluster maps · spatial lag/error regression · LightGBM · SHAP · spatial block
cross-validation · Monte Carlo rank stability · block-group resolution · the six static
matplotlib figures · `config.yml` · the `src/` package tree · the `sql/` directory · the
`Makefile` · the test suite.

Two of these deserve a note rather than silence:

- **Spatial SQL** was in the old plan as a skill-building exercise, not because the
  architecture needed it. A dashboard reads one flat file — there is no query engine in that
  picture. If you want the skill on your résumé, do it as a separate one-evening project
  where it's actually the point.
- **Machine learning** was going to predict a temperature you already measured, which is a
  weak story. If it comes back in a v2, the useful form is either attribution ("missing
  canopy accounts for most of Bayview's excess heat") or clustering into named neighborhood
  types — not prediction, and not classification. Surface temperature is continuous; binning
  it into "hot / not hot" invents a threshold you then have to defend.

---

## Repo structure

```
CaliforniaHeatMapping/
├── README.md
├── PROJECT_PLAN.md
├── environment.yml
├── pipeline/
│   ├── smoke_test.py            # standalone check, not part of the run
│   ├── boundaries_acs.py        # neighborhoods + demographics
│   ├── landsat.py               # surface temperature + fog composites
│   └── join_export.py           # zonal stats, index, write the JSON
├── data/
│   ├── raw/                     # downloads, gitignored
│   └── neighborhoods.json       # THE artifact, committed
├── docs/                        # named "docs" because GitHub Pages serves only
│   ├── index.html               #   the repo root or /docs — not /site
│   ├── data.js                  # the artifact as a script, so file:// works
│   └── neighborhoods.json       # copy of the artifact
└── resources/                   # reference material, unchanged
```

Three scripts. One page. No package tree, no config file — constants live at the top of the
script that uses them.

---

## Data sources

All verified reachable and downloadable on 2026-08-02 except where noted.

| Layer | Source | Notes |
|---|---|---|
| Neighborhood boundaries | DataSF `j2bu-swwd` | ✅ 41 polygons, fields `nhood` + geometry |
| Tract → neighborhood crosswalk | DataSF `sevw-6tgi` | ✅ 242 tracts, joins on `geoid` |
| Demographics | Census Reporter API, ACS (American Community Survey) 2024 5-year, tract level | ✅ **no API key required** |
| Surface temperature | Landsat Collection 2 Level-2, via Planetary Computer STAC (SpatioTemporal Asset Catalog) | ✅ proven by the smoke test |
| Vegetation / canopy | NDVI computed from the same Landsat scenes | Replaces the DataSF canopy layer — see below |
| Validation reference | CDC/ATSDR Social Vulnerability Index, `data.cdc.gov/ypqf-r5qs` | Replaces the SF DPH index — see below |

**The SF DPH (Department of Public Health) Heat Vulnerability Index is not usable, and the
plan changed because of it.** It is not on DataSF (zero search results); `sfclimatehealth.org`
no longer resolves at all; the sf.gov dashboard page publishes charts but no download and
directs inquiries to `climateandhealth@sfdph.org`; and the one live artifact — an ArcGIS
feature service, `Census_Blocks_with_Climate_Change_Vulnerability_Indicators` — exposes its
columns as `cr1` through `cr6` and `sum` with no data dictionary. Correlating against
undocumented columns proves nothing.

Validate against the **CDC Social Vulnerability Index** instead. It is tract-level, fully
documented, stably hosted, and a closer comparator anyway: it covers the sensitivity and
adaptive-capacity half of the index, so agreement there is a real check while the exposure
half stays legitimately yours. **CalEnviroScreen 4.0** (OEHHA feature service) is a good
second reference if you want one.

> Worth emailing `climateandhealth@sfdph.org` anyway — it costs five minutes, and if they send
> the real index it becomes a strong README line. Just don't let the weekend depend on it.

---

## Variables

**Exposure** — summer surface temperature, tree canopy percent, fog frequency

**Sensitivity** — percent 65+, percent under 5, percent below poverty

**Adaptive capacity** (inverted) — median household income, percent renters, percent
linguistically isolated households

**Overlay, not scored** — race and ethnicity composition

**Index** — percentile-rank each variable across the 41 neighborhoods, average within each of
the three components, then take the **geometric mean** across components. Geometric because
it's multiplicative: a neighborhood can't fully compensate for extreme exposure by scoring
well on capacity.

The canopy variable is **NDVI from the Landsat scenes**, not the DataSF canopy layer. That
layer is 289,219 polygons — too heavy for this scope — and street tree points would miss every
park and back yard, which is most of the city's shade. NDVI comes from the same sensor and the
same dates as the temperature, needs no new source, and its per-neighborhood range (0.07 to
0.57) separates the dense east from the green west cleanly.

---

## Findings

Computed over the 38 scored neighborhoods. **These changed the narrative** — the plan was
written assuming heat tracks income, and it does not.

| Relationship | Pearson | Spearman |
|---|---|---|
| heat vs fog | **−0.79** | |
| heat vs canopy | **−0.81** | |
| heat vs median income | −0.11 | −0.19 |
| heat vs % people of color | +0.13 | +0.18 |
| fog vs median income | +0.15 | +0.17 |
| fog vs % people of color | −0.03 | −0.05 |
| canopy vs median income | **+0.40** | **+0.50** |
| canopy vs % people of color | **−0.37** | **−0.52** |
| index vs % people of color | **+0.53** | |

**The story the data actually tells.** Surface heat in San Francisco is governed almost
entirely by fog and vegetation. Fog is indifferent to who lives under it — near-zero
correlation with both income and race — and it is strong enough to swamp everything else,
which is what flattens the heat-versus-income relationship that shows up in most American
cities. But the part of the picture that is built rather than given, tree canopy, is
distributed along income and racial lines. And the composite index, which asks who can cope
rather than only who is hot, still lands at +0.53 with race.

By income quartile the heat anomaly runs +0.25, +0.34, −0.29, −0.42 °C from poorest to
richest — a real ordering, but roughly 0.7 °C of spread against an 11 °C citywide range.

Top of the index: Chinatown, Tenderloin, Japantown, South of Market, Western Addition.
Bottom: Seacliff, Lakeshore, Presidio, Glen Park.

> Lakeshore ranks 37th of 38 despite 24.8% poverty, because it is foggy and green. That is not
> a bug — it is the index working, and it is worth calling out on the page.

---

## Schedule

### Friday · 2h — pay the setup cost now — ✅ DONE

The single most likely way this weekend fails is a broken GDAL install at 9am Saturday. It
nearly did.

**Environment: `sfheat`, and it is a deliberate hybrid. Do not "clean it up."**

This machine runs Windows 11 Home with **Smart App Control enforced**
(`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy` → `VerifiedAndReputablePolicyState = 1`).
Smart App Control refuses to load unsigned binaries based on a reputation check, and it hits
scientific Python hard — but inconsistently, per binary:

| | conda-forge | PyPI wheel |
|---|---|---|
| pandas, geopandas, shapely, pyproj, exactextract | **works** | blocked |
| rasterio, rioxarray, pyogrio, pyarrow | blocked | **works** |

The blocked list grew as the pipeline touched new code paths — `pyogrio` surfaced on the first
`read_file`, `pyarrow` on the first `to_parquet`. Expect more, and expect the same fix. The
tell is always `An Application Control policy has blocked this file`.

Neither a pure-conda nor a pure-pip environment can work. The hybrid does, because the two
failure sets don't overlap:

```
mamba create -n sfheat python=3.11 geopandas pystac-client planetary-computer \
  exactextract pandas requests matplotlib -c conda-forge

# These MUST come from pip, and MUST use --no-deps so pip cannot pull in the
# blocked pandas/numpy wheels over the working conda ones. --force-reinstall is
# required because pip otherwise sees conda's copy and does nothing.
python -m pip install --force-reinstall --no-deps rasterio rioxarray pyogrio pyarrow
```

Verified working together in one process: rasterio 1.4.4 (bundled GDAL 3.10.3), pandas 3.0.5,
geopandas 1.1.4, pyproj 3.7.2. Coordinate transforms agree across the two PROJ copies.

> Turning Smart App Control off would also fix this, and it is **irreversible** — Windows
> cannot re-enable it without a full reinstall. The hybrid avoids that entirely, so don't.

**Smoke test — passed.** `pipeline/smoke_test.py` found 30 summer-2023 scenes, read the
clearest (0.1% cloud) over the SF window, and produced a recognizable thermal image of the
city: Golden Gate Park, the Presidio, and Lake Merced all read cool; pavement reads hot.
It rehearses the exact pattern `landsat.py` needs, so the risky part of Saturday is proven.

**Data sources — verified.** The crosswalk exists and nests cleanly (see A1). The SF DPH Heat
Vulnerability Index does not exist in usable form, and the validation reference changed to the
CDC Social Vulnerability Index as a result (see Data sources).

**Census API key — abandoned, and not needed.** The key issued by api.census.gov never
activated; the API returns `Invalid Key` for it and `Missing Key` without one, so unkeyed
access to the official API is not an option either.

**Use the Census Reporter API instead — it needs no key.** One request returns every table
this project needs, for all SF tracts, with margins of error:

```
https://api.censusreporter.org/1.0/data/show/latest
  ?table_ids=B01003,B19013,B17001,B01001,B25003,C16002,B03002
  &geo_ids=140|05000US06075
```

| Table | What it gives |
|---|---|
| `B01003` | total population — the weighting denominator |
| `B19013` | median household income |
| `B17001` | poverty |
| `B01001` | age, for 65+ and under 5 |
| `B25003` | tenure, for renters |
| `C16002` | limited-English households |
| `B03002` | race and ethnicity — the overlay |

Verified returning ACS **2024** 5-year, which is a *more* current vintage than the official
API would have given. Geography IDs arrive as `14000US06075010101`; the last 11 characters are
the tract GEOID that joins to the DataSF crosswalk.

> Honest caveat for the README: this is a third-party service wrapping ACS, not the Census
> Bureau's own endpoint. It's a well-established project and fine for this, but say so rather
> than implying you hit the official API. If a working key ever arrives, swapping back is a
> one-function change.

**Nothing else is open. Friday is done.**

### Saturday · ~7h — build the artifact

**A1 · 1.5h — Boundaries and demographics** → `boundaries_acs.py`

Pull the 41 neighborhoods and reproject to EPSG:3310 immediately. Pull ACS tract data, join
via the crosswalk, aggregate to neighborhoods.

**This block is easier than budgeted — verified Friday.** Every one of the 242 tracts maps to
exactly one neighborhood, with no tract split across two and none unassigned. The analysis
neighborhoods were *built* by grouping census tracts, so they nest perfectly. That means no
areal weighting and no spatial interpolation: the aggregation is a `groupby` on the crosswalk.

One wrinkle, already identified: the crosswalk carries **42** neighborhood values, not 41. The
extra is `"The Farallones"` — the Farallon Islands, legally in SF County and 30 km offshore.
It has no matching polygon in the boundary file. Drop that single tract and the join is exact.
Say so in the README; it's a real analytical choice, not cleanup.

> Aggregate rates with **population weighting**, never a plain average of percentages. A
> 400-person tract and a 6,000-person tract do not get equal say in a neighborhood's poverty
> rate. This still applies — perfect nesting removes the *geometry* problem, not the
> *weighting* one. This is the kind of detail an interviewer looks for.

One count mismatch to expect: Census Reporter returns **244** tracts for SF County, the
crosswalk has **242**. Confirmed on the run — the three extras all have population zero, and
the crosswalk is authoritative, so inner-joining on it loses nothing.

**Three of the 41 neighborhoods are parks, and they must not be scored.** Golden Gate Park
(58 residents), McLaren Park (146), and Lincoln Park (160, and zero households) produce ACS
rates that are pure noise. Left alone, McLaren Park reads 75% poverty and 94% over-65 off ~115
households and tops the vulnerability index — making the dashboard's headline finding "San
Francisco's most heat-vulnerable community is a park."

They carry a `has_residents` flag instead of being deleted: the index, rankings, and scatter
skip them, while the polygons still render so the map covers the whole city. The threshold is
500, and the exact value is irrelevant — the next smallest neighborhood is the Presidio at
3,901, a 24x jump.

*Gate:* 41 rows, correct CRS (Coordinate Reference System), demographics attached, no nulls.
Assert the tract count is 241 after dropping the Farallones, and that the join lost nothing.

**A2 · 3h — Landsat** → `landsat.py`

The longest and riskiest block. One STAC query serves both outputs — get this right or you
re-download everything.

```python
# Do NOT filter on cloud cover here.
# Clear scenes build the temperature composite. ALL scenes build fog frequency.
search = catalog.search(
    collections=["landsat-c2-l2"],
    bbox=SF_BBOX,
    datetime="2019-06-01/2024-09-30",
    query={"platform": {"in": ["landsat-8", "landsat-9"]}},
)
```

Filter to June–September. Expect 80–120 scenes; SF at 30m is only ~400×400 pixels, so
windowed reads keep this fast.

Per scene, read the thermal band and QA (Quality Assessment) band, then:

```python
LST_C = ST_B10 * 0.00341802 + 149.0 - 273.15
# QA_PIXEL bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow
cloud = (qa >> 1 & 1) | (qa >> 2 & 1) | (qa >> 3 & 1) | (qa >> 4 & 1)
```

Normalize each scene to its own citywide mean before compositing, then take the median. You
want the spatial pattern, not a record of which day was hottest.

Fog frequency is then free — you already have every cloud mask: `masks.sum(0) / len(masks)`.

**Run complete.** 125 summer scenes found, 63 kept (the rest cover less than half the window
and would skew their own citywide mean), producing `lst_anomaly.tif`, `lst_absolute.tif`,
`fog_frequency.tif`, and `clear_obs_count.tif` on a 503×522 grid at 30 m in EPSG:32610. The
scene stack is cached to `data/interim/scene_stack.npz`, so re-runs are instant.

Fog validation passed on the first try and is monotonic west to east — Outer Sunset 71.4%,
Richmond 66.7%, Twin Peaks 54.0%, Mission 34.9%, Bayview 31.7%. A 35.7-point gradient.

**Two bugs found by looking at the picture, both of which produced plausible wrong maps:**

1. **The QA water bit is only set on clear pixels.** Dividing water counts by all valid
   observations scores the ocean at ~0.35 — it is fogged most of the time, so it reads as
   cloud and never as water — leaving the bay unmasked at a 0.5 threshold. Unmasked water then
   entered each scene's citywide mean, and water runs ~20 °C colder than pavement, so every
   land anomaly was inflated. The denominator must be *clear* observations. Water mask went
   from 23.5% of the grid to 41.7%.
2. **The read window is a rectangle and contains Marin and the East Bay.** Those are land, so
   they joined the "citywide" baseline. Normalization now uses a rasterized San Francisco
   boundary — 133,250 px, or 119.9 km², against the city's actual 121 km².

> Neither would have thrown an error. Both were caught by rendering the arrays and looking at
> them. Budget the time to look at the picture.

*Gate:* two rasters. **Validate the fog layer against geography you already know:** high over
the Sunset and Richmond, dropping sharply at Twin Peaks, low over the Mission and Bayview. If
it doesn't look like that, you have a bit-order bug.

*Triage:* if this passes 4h, narrow to 2022–2024 and move on.

**A3 · 1h — Zonal statistics** → into `join_export.py`

`exactextract` both rasters to the 41 polygons. Add canopy percent. Mean and 90th percentile.

**A4 · 1h — Index and export**

Percentile-rank, compose, write `neighborhoods.json` in EPSG:4326 with simplified geometry
(the web needs ~100KB, not 20MB).

Then the validation check: correlate your index against the SF DPH one. Agreement is a
credibility line for the README; disagreement is more interesting and worth a paragraph
either way.

### Sunday · ~6h — build the page

**B1 · 4h — The dashboard** → `site/index.html` — ✅ DONE

**Zero dependencies.** No MapLibre, no plotting library, no build step. 41 polygons and a few
scatters are less code as inline SVG with a hand-rolled equal-area-ish projection than the CDN
boilerplate would be, and the page then works offline, on `file://`, and on Pages unchanged.
`join_export.py` emits `site/data.js` alongside the GeoJSON because `fetch()` is blocked under
`file://` — a `<script src>` is not.

**Colour, decided by the data's job rather than taste:**

- Heat is an anomaly around a true zero (the city average), so it gets the **diverging**
  blue↔red ramp with a neutral midpoint and breaks fixed at zero — a floating midpoint would
  let "average" drift.
- Everything else on the map is magnitude → **sequential blue**, quantile bins, reversed in
  dark mode so "more" always moves away from the surface.
- The ranked bars are **one colour for all 38**. Length already encodes the value; a ramp would
  double-encode it and burn the only free channel.
- The only multi-series mark is the race composition bar. Its five hues were run through the
  palette validator: passes in both modes (worst adjacent CVD ΔE 9.1 light / 8.4 dark). Light
  mode returns a sub-3:1 contrast warning on three hues, so it ships with a legend, in-bar
  labels where they fit, and the full data table as relief.

Correlations are computed in the browser from the data, so the prose can never drift from the
numbers. Verified rendering in both light and dark at 1493px.

*Superseded plan text follows.*

MapLibre for the map, Observable Plot for charts, both from a CDN. Data inlined or fetched
from the JSON beside it. Sections, in scroll order:

Section order follows the **lead-with-the-null-result** framing: show the expected pattern
failing, then explain why, then reveal the inequity that was hiding underneath it.

1. **Hook** — title, one-sentence thesis, three stat tiles
2. **Where it's hot** — choropleth with a metric toggle (heat / index / canopy / income)
3. **The pattern that isn't there** — heat versus income scatter, visibly flat, stated
   plainly: San Francisco does not behave like other cities
4. **Why — the fog** — fog map beside the heat map. The mechanism, and the fact that fog is
   distributed by geography, not by wealth or race
5. **What the fog hides** — canopy versus income and versus race. The inequity is in the part
   of the city that was built, not the part that was given
6. **What the satellite can't see** — the clear-sky bias, with the observation-count map: ~25
   clear looks in the west against ~45 in the east. The composite understates the west's
   coolness, so the true fog effect is larger than shown
7. **Every neighborhood** — all 38 scored, ranked bars, with the three parks shown as unscored
8. **Pick one** — dropdown, profile card, that neighborhood versus the city median
9. **Method and limits** — sources, how the index is built, the caveats below

Call out Lakeshore explicitly somewhere: 37th of 38 despite 24.8% poverty, because it is foggy
and green. It is the clearest single illustration that exposure and disadvantage come apart
here.

Cartography rules that cost nothing: sequential `magma` or `inferno`, never rainbow; state
the classification scheme; normalize any count before mapping it.

**B2 · 1h — Deploy.** Push, enable GitHub Pages on `/site`, confirm the live URL works on a
phone.

**B3 · 1h — README.** Thesis in the first paragraph, a screenshot, the live link, the
limitations, and one command to rebuild.

---

## Limitations — write these down, don't discover them in an interview

- **Surface temperature is not air temperature.** Landsat measures the skin temperature of
  roofs and pavement, which runs hotter than the air a person stands in. The spatial pattern
  is meaningful; the absolute values are not comparable to a weather station.
- **Daytime only.** Landsat passes mid-morning. Nighttime heat retention is the more
  health-relevant variable and this project doesn't measure it.
- **41 units is coarse.** Real inequity exists within neighborhoods and is invisible here —
  the modifiable areal unit problem, stated plainly.
- **Clear-sky bias.** The composite can only see through cloud, so the foggy west side is
  measured on its least foggy days. This systematically *understates* the true east-west
  gradient. The fog layer is what lets you say that with a number attached.
- **ACS margins of error** at tract level are wide for small populations.
- **Median household income at neighborhood level is an approximation.** Medians cannot be
  summed or averaged honestly, so neighborhood values are a household-weighted mean of the
  tract medians. Every other rate is exact — computed as summed numerator over summed
  denominator, which is what population weighting actually means. Say which is which.
- **Three neighborhoods are parks and carry no score** — Golden Gate Park, McLaren Park, and
  Lincoln Park. They appear on the map as "no residential population," never in a ranking.

---

## If you want to keep going

In order of value, each about one evening, none required:

1. Spatial autocorrelation — Moran's I and a LISA cluster map on the temperature field
2. Neighborhood typology — cluster the 41 into 5–6 named types
3. Canopy cooling attribution — how much cooling does a percentage point of canopy buy, and
   does it depend on fog exposure
4. Block-group drill-down inside the existing dashboard
5. ECOSTRESS night composites — the nighttime story, gated on an AppEEARS order
