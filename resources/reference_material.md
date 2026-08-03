# `resources/` — reference material

Background reading, source links, and notes. Nothing here is consumed by code — that's `config/` and `data/`.

| File | Contains |
|---|---|
| `data_sources` | Working list of portals, endpoints, and access notes |

Once a source is actually downloaded, its authoritative record goes in `docs/data-dictionary.md` with retrieval date, license, and native coordinate reference system. This folder is for the *scouting* stage; the dictionary is for provenance.

## Primary data portals

| Layer | Where |
|---|---|
| Landsat Collection 2 Level-2 | Microsoft Planetary Computer STAC · AWS Earth Search · USGS EarthExplorer |
| ECOSTRESS `L2T_LSTE` | NASA LP DAAC / AppEEARS — **order latency of hours to days, request early** |
| SF Urban Tree Canopy | DataSF · `55pv-5zcc` |
| SF Street Tree List | DataSF · `tkzw-k3nq` |
| Building footprints, parcels, zoning | DataSF · SF Planning |
| Cooling / resilience centers | SF.gov · DataSF |
| ACS 5-year, block group | Census API |
| TIGER/Line boundaries | Census, via `pygris` |
| Impervious surface | National Land Cover Database (MRLC) |
| Digital elevation model | USGS 3D Elevation Program |
| High-resolution imagery | National Agriculture Imagery Program (NAIP) |
| Official Heat Vulnerability Index | SF Department of Public Health · `sfclimatehealth.org` |
| Ground-truth air temperature | NIHHIS/CAPA heat-watch SF campaign — **likely needs an email request** |
| Historical redlining | Mapping Inequality (HOLC) |
| Street network | OpenStreetMap, via OSMnx |

## Long-lead requests

Two items gate the most interesting parts of the analysis and cannot be compressed by working faster:

1. **NASA Earthdata account + AppEEARS ECOSTRESS order** — gates the nighttime heat-retention story (`P2.C3`), which is the most novel finding available.
2. **CAPA/NIHHIS traverse data** — gates ground-truth validation (`P11.C1`), the only way to quantify rather than merely assert the surface-versus-air-temperature caveat.

Submit both before you need them. Everything else in this project is available on demand.

## Background worth reading

- Torregrosa et al. on building a coastal California fog climatology from satellite cloud masks — the published precedent for the approach in `P3.C1`
- The 2017 Labor Day San Francisco heat event — the canonical local case, and the reason the heat-event composite in `P2.C4` matters
- Literature on nighttime heat and mortality — why the day–night differential, not peak daytime temperature, is the health-relevant variable
