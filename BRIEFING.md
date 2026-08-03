# Briefing — everything in this project, explained

Written to be read before talking about it. Every number here is from
`data/*.json`, not from memory. Where something is a judgement call or a
weakness, it says so — those are the answers that land best anyway.

**The one rule to fall back on if you get lost:** everything upstream produces
`data/neighborhoods.json`; everything downstream displays it. If a question is
about where a number comes from, you are being asked about the pipeline. If it
is about how a number looks, you are being asked about the page.

---

## 1. The 30-second answer

> I measured surface temperature for San Francisco from six summers of Landsat
> imagery, joined it to census demographics at the neighborhood level, and built
> a heat vulnerability index. The headline is that San Francisco does not behave
> like other US cities — heat here is governed by fog and vegetation, not by
> income. Fog is handed out by geography, so it flattens the heat–poverty
> pattern you would find in Phoenix or Baltimore.

If they want one more sentence:

> The interesting part is that income isn't absent, it's cancelling. Income buys
> tree canopy and canopy cools, but wealthier ground here is also denser and more
> built, which heats. The two effects are roughly equal and opposite.

---

## 2. The pipeline, in order

Five scripts. Each is standalone and each prints what it did.

| Script | What it does | Output |
|---|---|---|
| `boundaries_acs.py` | Pulls 41 neighborhood polygons and the tract crosswalk from DataSF, pulls ACS tables from Census Reporter, aggregates tracts to neighborhoods | a GeoDataFrame in memory |
| `landsat.py` | Searches Planetary Computer for Landsat scenes, reads thermal + optical + QA bands, builds composites | `lst_anomaly.tif`, `ndvi_median.tif`, `ndbi_median.tif`, `fog_frequency.tif`, `clear_obs_count.tif` |
| `terrain.py` | USGS 3DEP elevation, derives slope and aspect | `elevation.tif`, `slope.tif`, `southness.tif` |
| `join_export.py` | Zonal statistics, builds the index, writes the artifact | `neighborhoods.json`, `docs/data.js`, `docs/stats.js` |
| `spatial_analysis.py` / `mediation.py` / `gwr.py` | The statistics, at **tract** level (n = 232), not neighborhood level | `spatial_results.json`, `mediation_results.json`, `gwr_results.json` |

**Why two spatial resolutions.** The page shows 41 neighborhoods because names
tell a story and GEOIDs don't. The statistics run on 232 tracts because n = 38
is too small — see §7, "the power problem." Say this before they ask; it looks
like carelessness if they find it themselves.

---

## 3. Libraries — what each one is for

### Geospatial

| Library | What it does | Why it's here |
|---|---|---|
| **geopandas** | pandas with a geometry column | Everything tabular-with-shapes. Joins, reprojection, dissolve |
| **shapely** | geometry operations | Under geopandas; used directly to convert geometries to GeoJSON |
| **pyproj** | coordinate reference system transforms | Reprojecting between the three CRSs below |
| **rasterio** | reading and writing raster files, windowed reads | All the Landsat and elevation work |
| `rasterio.vrt.WarpedVRT` | reprojects a raster on the fly during read | Landsat scenes arrive in different UTM zones; this puts every scene on one canonical grid without writing intermediates |
| `rasterio.features.rasterize` | turns polygons into a label array | Zonal statistics, and rasterizing the SF boundary |
| **pystac-client** | queries a STAC catalog | Finding which Landsat scenes cover SF in a date range |
| **planetary-computer** | signs asset URLs | Microsoft's Landsat mirror needs a signed URL to read |

### Statistics

| Library | What it does | Why it's here |
|---|---|---|
| **numpy / pandas** | arrays and dataframes | Everything |
| **libpysal** | spatial weights matrices | Defines "neighbor" — Queen contiguity and k-nearest |
| **esda** | exploratory spatial data analysis | Moran's I and LISA |
| **spreg** | spatial regression | OLS with spatial diagnostics, spatial lag and spatial error models |
| **mgwr** | geographically weighted regression | Fitting a separate local model at every tract |
| **statsmodels** | general statistics | Variance inflation factors |
| **scikit-learn** | machine learning | Only KMeans, and only briefly — see §9 |
| **threadpoolctl** | controls BLAS threading | A workaround, not a design choice — see §10 |
| **matplotlib** | plotting | Diagnostic images during development. **Nothing on the published page uses it** |

### Frontend

**None.** The page is one HTML file with inline SVG and hand-written
JavaScript. No MapLibre, no D3, no build step, no CDN.

> **"Why no mapping library?"** 41 polygons and a few scatter plots are less
> code as inline SVG than the CDN boilerplate would be, and the page then works
> offline, from `file://`, and on GitHub Pages unchanged. The projection is about
> fifteen lines. If this were 40,000 polygons or needed pan and zoom over tiles,
> that answer flips and I'd reach for MapLibre.

### Deliberately not used

- **exactextract** — it's in `environment.yml` but the zonal statistics are
  hand-rolled with `rasterize` + a boolean mask. At 30 m pixels against
  neighborhoods of several km², fractional edge pixels move nothing. Be ready to
  say that; it's in the docstring at `join_export.py:79`.
- **PostGIS / spatial SQL** — a dashboard reads one flat file. There is no query
  engine in that architecture. It was in an earlier plan as skill-building, which
  is a bad reason to add a database.
- **Machine learning for prediction** — predicting a temperature I already
  measured is a weak story. Regression here is for *explanation*, not prediction.

---

## 4. Coordinate reference systems

Three, each with a job. Expect to be asked why you didn't just use one.

| EPSG | Name | Used for |
|---|---|---|
| **32610** | UTM zone 10N | The raster grid. Metres, minimal distortion over SF, matches Landsat's native projection |
| **3310** | California Albers | Area and distance calculations. Equal-area, so km² is honest |
| **4326** | WGS 84 lat/lon | Storage and the web. What GeoJSON expects |

---

## 5. The measured variables

Every formula below is exactly what the code does.

### heat_anomaly — °C

```
per scene   LST°C   = ST_B10 · 0.00341802 + 149.0 − 273.15
            anomaly = LST°C − mean(LST°C over cloud-free SF land, same scene)
per pixel   median(anomaly) across scenes where that pixel was cloud-free
            requires ≥ 5 such scenes, otherwise no value
per nhood   mean over the neighborhood's pixels
```

- `0.00341802` and `149.0` are the **published Collection 2 Level-2 scaling
  factors** from USGS, not fitted. `273.15` is Kelvin to Celsius.
- **Why normalise per scene:** without it, a hot *day* reads as a hot
  *neighborhood*. The composite would be a record of which dates were sampled.
- **Why median not mean:** one undetected cloud edge would drag a mean; a median
  ignores it.
- Source: Landsat Collection 2 Level-2, asset `lwir11` (band ST_B10). 63 summer
  scenes, 2019–2024.

### ndvi — −1 to 1

```
reflectance = DN · 0.0000275 − 0.2
ndvi = (nir08 − red) / (nir08 + red), clipped to [−1, 1]
then median across cloud-free scenes, mean over the neighborhood's pixels
```

Normalised Difference Vegetation Index. Healthy vegetation reflects strongly in
near-infrared and absorbs red, so the ratio separates plants from everything
else. Stands in for tree canopy.

> **"Why not use a real canopy layer?"** DataSF publishes one — 289,219 polygons,
> too heavy for this scope — and the street-tree point layer misses every park and
> back yard, which is most of the city's shade. NDVI comes from the same sensor
> and the same dates as the temperature. The honest cost: NDVI cannot tell a tree
> from a lawn, and a lawn does not shade a sidewalk.

### ndbi — −1 to 1

```
ndbi = (swir16 − nir08) / (swir16 + nir08)
```

Normalised Difference Built-up Index. Built surfaces reflect shortwave infrared
more than near-infrared. Used as a **control** in the models — it never appears
on a map.

### fog — share 0 to 1

```
cloud = QA_PIXEL bits 1 | 2 | 3 | 4   (dilated cloud, cirrus, cloud, cloud shadow)
valid = not fill
fog   = Σ cloud / Σ valid
```

- Built from the observations the temperature composite **throws away**. A scene
  useless for heat is a data point for fog. This is the cheapest interesting
  thing in the project — the cloud masks were already in memory.
- The cloud flags come from USGS's own CFMask algorithm. Not my classification —
  worth saying, because it's the one raster threshold that isn't a judgement call.
- Range across neighborhoods: **35% to 74%**, monotonic west to east.

### median_income — USD

```
Σ(B19013_001 · B25003_001) / Σ(B25003_001)
```

A household-weighted mean of tract medians. **This is the one approximate
column** — medians cannot be summed. Every other rate is exact.

### The five pct_* rates — %

```
100 · Σ(numerator) / Σ(denominator)   across the neighborhood's tracts
```

| Column | ACS table |
|---|---|
| `pct_65_plus` | B01001, sum of the 65+ age brackets, male and female |
| `pct_under_5` | B01001_003 + B01001_027 |
| `pct_poverty` | B17001_002 / B17001_001 |
| `pct_renter` | B25003_003 / B25003_001 |
| `pct_limited_english` | C16002 lines 004, 007, 010, 013 |

> **The point to make here:** summed numerator over summed denominator, never an
> average of tract rates. An average of rates gives a 400-person tract the same
> weight as a 6,000-person one. This is population weighting, and it is exactly
> the kind of detail that gets checked.

**No racial or ethnic variable exists anywhere in this project** — not collected,
not computed, not displayed. If asked why: the index is about heat exposure and
the capacity to cope with it, and it stands on those variables alone.

---

## 6. The index

```
index = ( exposure · sensitivity · capacity_gap )^(1/3)

exposure     = 100 · [ ρ(heat_anomaly) + ρ(−ndvi) + ρ(−fog) ] / 3
sensitivity  = 100 · [ ρ(pct_65_plus) + ρ(pct_under_5) + ρ(pct_poverty) ] / 3
capacity_gap = 100 · [ ρ(−median_income) + ρ(pct_renter) + ρ(pct_limited_english) ] / 3

ρ(x) = rank(x, method='average') / n   among the n = 38 scored neighborhoods
```

- **ρ lands in (0, 1] and never 0.** A zero would annihilate the product. This is
  why `method='average'` and division by `n` rather than `n−1`.
- **Negation** orients a variable so higher always means more vulnerable. More
  fog is *protective*, so it enters as `−fog`.
- **Geometric mean, not arithmetic.** Multiplicative means a neighborhood cannot
  fully buy off extreme exposure with strong adaptive capacity. That is the whole
  argument for a vulnerability index rather than three separate maps.

### What is yours and what is not

**Not original:** heat vulnerability indices are an established public-health
tool, and the exposure / sensitivity / adaptive-capacity decomposition is the
conventional climate-vulnerability framing.

**Specific to this project:** the choice of these nine variables, weighting them
all equally, weighting the three components equally, and combining them
geometrically. Another analyst would reasonably choose differently and get a
different ranking. **Ranks a few places apart should be read as ties.**

**It has not been validated against any outcome.** A working index would be
tested against heat-related emergency visits or mortality. This one is tested
against nothing. It is a defensible composite of plausible variables, not a
demonstrated predictor of harm.

> If you say only one thing about the index, say that last paragraph. Volunteering
> it is much stronger than conceding it.

---

## 7. The statistics

All at tract level, n = 232.

### Correlations

| Pair | r |
|---|---|
| heat ~ fog | **−0.778** |
| heat ~ ndvi | **−0.619** |
| heat ~ median income | −0.125 |
| heat ~ pct_poverty | **+0.197** |
| ndvi ~ median income | **+0.410** |
| ndvi ~ pct_poverty | **−0.360** |

At the 38-neighborhood level heat ~ income is −0.11 and heat ~ fog is −0.79.

### The power problem — raise this yourself

At n = 38 the smallest correlation distinguishable from zero is **|r| ≥ 0.32**.
The heat-vs-income confidence interval was [−0.42, +0.22] — an interval that wide
cannot support "there is no relationship." Moving to 232 tracts drops the floor to
**0.13**, and at that resolution poverty resolves at +0.20.

> This is the single best thing in the project to volunteer. A null result at
> n = 38 is not a finding, it is an absence of evidence, and knowing the
> difference is the point.

### Moran's I — is the map clustered?

**What it is:** a correlation coefficient for space. It asks whether a tract's
value resembles its neighbors' values more than chance would allow. Ranges about
−1 to +1; near 0 means random.

| Variable | I | p |
|---|---|---|
| fog | **+0.869** | 0.001 |
| heat | **+0.563** | 0.001 |
| ndvi | +0.511 | 0.001 |
| median income | +0.493 | 0.001 |
| pct_poverty | +0.419 | 0.001 |

Neighbors are defined by **Queen contiguity** — tracts sharing any edge or corner.

**Why it matters:** clustered residuals break the independence assumption of OLS,
so the standard errors are wrong and the p-values are too small. Finding I > 0
is what forces everything that follows.

### LISA — where is it clustered?

Local Indicators of Spatial Association: Moran's I computed per tract, so you get
a map of clusters instead of one number.

| Class | Tracts |
|---|---|
| not significant | 196 |
| hot spot (high surrounded by high) | 30 |
| cool spot | 5 |
| cool outlier | 1 |

Significance is **FDR-corrected** — cutoff p < 0.005, not 0.05. With 232
simultaneous tests, uncorrected testing would produce roughly 12 false positives
by chance.

### The model sequence

| Model | R² | Residual Moran's I |
|---|---|---|
| demographics only (income + poverty) | **0.040** | +0.510 |
| + canopy | 0.412 | +0.641 |
| + fog | **0.766** | +0.485 |
| + terrain (elevation, slope, southness) | 0.784 | +0.484 |
| + built-up (ndbi) | 0.801 | +0.431 |

Two things to read off this:

1. **Demographics explain 4% of heat variance.** Physical geography explains
   roughly twenty times as much.
2. **The residual clustering was never explained.** It falls only 0.510 → 0.431
   across every covariate added — a 15.6% reduction. Something spatially
   structured is still missing. Candidates: building height and shadowing, coastal
   advection at finer scale, materials. **This is an open failure and it is on the
   page.** Say so.

### Lagrange Multiplier diagnostics

**What they are:** tests that tell you *which kind* of spatial dependence you have
— a spatial lag (my outcome depends on my neighbors' outcomes) or a spatial error
(an omitted spatially-structured variable). Both p-values were tiny; the error
form was far stronger, so a **spatial error model** was fitted.

### The spatial error model

```
λ = 0.657      pseudo-R² = 0.776
```

λ is the strength of spatial correlation in the errors. At 0.657 it is
substantial, which is another way of saying the missing variable is real.

Coefficients under this model: fog −10.42, ndvi −9.76, ndbi +4.93, elevation
+0.009, income +0.019, poverty −0.001.

### VIF — is anything collinear?

Variance Inflation Factor. Rule of thumb: above 5 is a concern, above 10 serious.

Highest here: **ndvi 4.72**, **ndbi 4.00**, income 2.49, poverty 2.31. All fine.
The ndvi/ndbi pair is the one to watch — they measure opposite sides of the same
ground.

### Mediation — the interesting result

The question: does canopy carry income's effect onto heat?

```
income  --a-->  canopy  --b-->  heat
   \____________ c' ___________/
```

With geography controls (fog, elevation, slope), **spatial block bootstrap**
confidence intervals:

| Path | Estimate | 95% CI |
|---|---|---|
| indirect (income → canopy → heat) | **−0.032** °C per $10k | [−0.055, −0.005] |
| direct (income → heat) | **+0.041** °C per $10k | [+0.007, +0.072] |

**Both significant, opposite signs.** This is *inconsistent mediation*: income
buys canopy and canopy cools, while income independently raises heat because the
wealthiest ground here is also the densest and most built. Net effect
indistinguishable from zero — which is why the raw correlation looks like nothing.

**Why a spatial block bootstrap:** neighboring tracts are not independent, so
resampling tracts individually gives intervals that are far too narrow. Blocks of
geography are resampled instead, preserving local correlation structure. Both
naive and spatial intervals are computed and reported so the difference is visible.

**Two honest caveats, both worth volunteering:**

1. **Poverty does not survive.** Its indirect path is +0.014 with a naive CI of
   [+0.0037, +0.0278] but a spatial CI of [−0.0038, +0.0285]. Naive says
   significant; spatial says no. The spatial one is correct.
2. **The income result reverses if built-up joins the controls.** With ndbi
   included, the indirect path collapses to −0.0003, CI [−0.0086, +0.0119]. Both
   specifications are published. Including ndbi risks over-controlling — it can
   absorb the very variation the mediator carries — and excluding it risks
   attributing to canopy what is really pavement. Neither is obviously right.

### GWR — geographically weighted regression

**What it is:** instead of one coefficient for the whole city, fit a separate
weighted regression at every tract using only nearby tracts. It answers "does
this relationship vary across space?"

| | |
|---|---|
| bandwidth | **50 tracts**, adaptive, chosen by AICc |
| GWR R² | 0.938 |
| global OLS R² | 0.753 |
| AICc improvement | 233 |
| tracts with significant local canopy effect | 200 of 232 |
| median canopy coefficient | −8.79 °C per unit NDVI |
| cooling effect vs fog | r = **+0.657** |

**The finding:** canopy buys the least cooling exactly where it is foggiest. The
west side already gets its cooling free from marine air, so planting there
returns less per tree than planting in the east.

**Adaptive bandwidth** means the 50 *nearest* tracts, not a fixed radius — so
dense downtown tracts and sparse western ones get comparable sample sizes.

**A trap I fell into, worth telling:** the local condition number had a median of
**30.0**, which looks like severe local collinearity. It was a units artifact —
the predictors were on wildly different scales. Standardising them dropped the
median to **7.54**, and the share of tracts above 30 fell from 50% to 1.3%. The
relationship holds at r = +0.54 among well-conditioned tracts only.

---

## 8. Numbers to know cold

| | |
|---|---|
| Neighborhoods | 41 mapped, **38 scored** |
| Why 3 unscored | Golden Gate Park, McLaren Park, Lincoln Park — 58 to 160 residents |
| Tracts | 232 in the analysis (242 in the crosswalk, minus the Farallones and low-population) |
| Landsat scenes | **125 found, 63 kept** |
| Date range | June–September, **2019–2024** |
| Resolution | 30 m |
| Heat spread | **9.7 °C** coolest to hottest neighborhood |
| Fog spread | **39 points**, 35% to 74% |
| Hottest | South of Market, **+3.46 °C** |
| Highest index | Chinatown, **79** of 100 |
| ACS vintage | 2024 5-year |

---

## 9. Things that went wrong — tell these stories

Interviewers remember these more than results.

**1. The water mask was undercounting, and it inflated every land temperature.**
The QA water bit is only set on *clear* pixels. Dividing water counts by all valid
observations scored the ocean at ~0.35 — it's fogged most of the time, so it reads
as cloud, never as water — leaving the bay unmasked at a 0.5 threshold. Unmasked
water then entered each scene's citywide mean, and water runs ~20 °C colder than
pavement, so every land anomaly was inflated. **The fix:** the denominator must be
*clear* observations. Water mask went from 23.5% of the grid to 41.7%.

**2. The read window is a rectangle, and it contained Marin and the East Bay.**
Those are land, so they joined the "citywide" baseline. Fixed by rasterizing the
actual SF boundary — validated at 119.9 km² against the city's real 121 km².

> **Neither of these threw an error.** Both produced plausible-looking maps and
> were caught by rendering the arrays and looking at them. That is the lesson.

**3. Three parks would have topped the vulnerability index.** McLaren Park reads
75% poverty and 94% over-65 off about 115 households — pure ACS noise on a tiny
population. Left alone, the headline finding would have been "San Francisco's most
heat-vulnerable community is a park." They carry a flag rather than being deleted,
so they still render on the map but never rank. The threshold is 500 residents and
the exact value doesn't matter: the next smallest neighborhood is the Presidio at
3,901, a 24× jump, so anything from 200 to 2,000 selects the same three.

**4. The Census API key never worked.** The key issued by api.census.gov never
activated — `Invalid Key` with it, `Missing Key` without. Switched to the **Census
Reporter API**, which needs no key and returned a *more* current vintage (ACS
2024). Be honest that this is a third-party wrapper around ACS, not the Bureau's
own endpoint.

**5. The GWR condition number scare** — see §7. A diagnostic that looks alarming
and is actually a units artifact.

**6. A colour bug that made weak correlations look strong.** Reducing opacity on a
dark surface makes colours *darker*, so the correlation matrix rendered weak
values as deep red. Fixed with a neutral fill rather than transparency.

---

## 10. The environment — expect this question if they see `environment.yml`

This machine runs **Windows 11 with Smart App Control enforced**. It refuses to
load unsigned binaries based on a reputation check, and it hits scientific Python
inconsistently — per binary, not per package manager:

| | conda-forge | PyPI wheel |
|---|---|---|
| pandas, geopandas, shapely, pyproj | **works** | blocked |
| rasterio, rioxarray, pyogrio, pyarrow | blocked | **works** |

Neither a pure-conda nor a pure-pip environment can work. The hybrid does, because
the failure sets don't overlap:

```bash
conda env create -f environment.yml
python -m pip install --force-reinstall --no-deps rasterio rioxarray pyogrio pyarrow
```

`--no-deps` is load-bearing — without it pip replaces the working conda pandas and
numpy with wheels that won't import. `--force-reinstall` too — without it pip sees
conda's copy and silently does nothing.

`sklearn_compat.patch_threadpool()` exists for the same reason: KMeans faulted on
this machine through its BLAS threading layer, and `threadpolctl` pins it to a
single thread as a workaround.

> Turning Smart App Control off would also fix it, and it is **irreversible** —
> Windows cannot re-enable it without a full reinstall.

---

## 11. Limitations — say these before you're asked

- **Surface temperature is not air temperature.** Landsat measures the skin
  temperature of roofs and pavement, which runs hotter than the air a person
  stands in. The spatial pattern is meaningful; absolute values are not comparable
  to a weather station.
- **Daytime only.** Landsat passes late morning. Nighttime heat retention is the
  more health-relevant variable and this doesn't measure it.
- **Clear-sky bias.** The composite can only see through cloud, so the foggy west
  is measured on its least foggy days. Every neighborhood is biased warm, +0.43 to
  +1.25 °C, correlating r = +0.58 with fog. **Crucially, the bias runs *against*
  the headline null rather than creating it** — correcting it would make
  heat-vs-income slightly more negative, not closer to zero.
- **41 units is coarse.** Inequity within neighborhoods is invisible — the
  modifiable areal unit problem.
- **The index is unvalidated.** See §6.
- **Residual spatial clustering is unexplained.** See §7.
- **Median income is approximate.** See §5.
- **Census Reporter is a third-party wrapper**, not the Census Bureau's endpoint.

---

## 12. Likely questions, with answers

**"Why neighborhoods and not tracts?"**
The page uses neighborhoods because names carry meaning — "Bayview" tells a story,
"060750231021" doesn't. The statistics use tracts because n = 38 can only resolve
|r| ≥ 0.32, which is too coarse for the questions being asked.

**"Why is this better than just downloading a heat vulnerability index?"**
It isn't necessarily — SF DPH publishes one. But theirs isn't obtainable: the
domain no longer resolves, the dashboard has no download, and the one live
artifact exposes columns named `cr1` through `cr6` with no data dictionary.
Correlating against undocumented columns proves nothing. Building it means every
threshold is inspectable.

**"How would you validate this?"**
Against heat-related emergency department visits or mortality, at tract level, with
a lag. Failing that, the CDC Social Vulnerability Index covers the sensitivity and
adaptive-capacity half, so agreement there would be a partial check while the
exposure half stays independent. **I haven't done either.**

**"What would you do with more time?"**
Explain the residual clustering — most likely building height and shadowing from a
3D city model. That's the concrete open problem.

**"Is this reproducible?"**
Yes — five scripts, run in order, all constants at the top of the file that uses
them. The Landsat scene stack caches to `.npz` so re-runs are instant. The one
thing that isn't pinned is the ACS vintage; Census Reporter serves "latest."

**"What's the hardest bug you hit?"**
The water mask. See §9 — it's the best story because the wrong answer looked
completely reasonable.

**"Why no tests?"**
There aren't any, and for a pipeline this size the validation is the assertions
and the printed gates in each script — tract counts, join completeness, the fog
gradient checked against geography I already know. For production I'd want real
tests around the QA bit unpacking and the aggregation weighting, which are the two
places a silent error does the most damage.

---

## 13. If you don't know

Say so, then say what you'd do to find out. Every number in this document is
checkable from `data/*.json` in about ten seconds, and saying "let me pull it up"
is a stronger answer than a confident wrong number.
