# REPORT.md — Observed Runtime Behaviour

> Updated on each essential code change. Documents what the code *actually* does when run.

---

## Data Collection

| Field | Value |
|---|---|
| Last run | — |
| Scenes downloaded | — |
| Storage used | — |
| Date range | — |
| Scenes evicted | — |

## Known Issues

- Initial run on empty catalog: first download may take several minutes (4 × ~30 MB JP2 per scene).
- Cloud cover filter reduces available scenes; if no scene found within `search_days_back` days below `cloud_cover_max`%, increase both thresholds.
- CDSE token endpoint has occasional 5xx outages; retry after 60 s.

## Test Results

Run `pytest tests/ -v` and paste summary here after each significant change.

```
(not yet run)
```
