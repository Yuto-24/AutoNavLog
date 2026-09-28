# Issue #161 portable model-selection fixtures

`generate_forecast_policy.py` produces `forecast-policy/` through the upstream
`jma-gpv-weather` native/public portable APIs. These are **synthetic acceptance
data, not operational forecasts**. No generator or fixture ships in a production
Local build.

- GSM: two real-format Run/file identities on 2026-09-15, native public prepare
  orchestration with a substituted synthetic GRIB decoder; 2026-09-16 00–09Z,
  31–35N / 130–133E. Wind is a known constant vector and HGT is monotone.
- MSM: captured #144 portable records; variants intentionally lower HGT to
  exclude the requested flight altitudes, exclude only the newest Run, or replace
  wind source values with NaN. The synthetic listings offer exactly these two captured Run identities, with complete
  URL keys. Production discovery is never filtered to generated assets.
- Tests refresh only delivery catalog lifetime, never Run IDs or forecast times.
- Desktop/Local canonical comparison includes model, Run and forecast provenance.
  Independent upstream tests compare native and actual Node/Chromium Pyodide GSM
  query values, source hashes, errors and the FH132 resolution transition.

Regenerate after installing the pinned vendor dependency:

```sh
python tests/fixtures/generate_forecast_policy.py
```

The snapshots are not a claim of live RISH/GSM acquisition, full-domain payload
sizes, or mobile memory acceptance. Those operational checks need fresh data and
the target deployment/device.
