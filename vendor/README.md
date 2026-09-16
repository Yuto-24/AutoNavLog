# Weather dependency

Production uses `jma_gpv_weather-0.5.0-py3-none-any.whl`, built from
[Yuto-24/jma-gpv-weather main](https://github.com/Yuto-24/jma-gpv-weather/commit/585bca6a912a5f61b9d15271761374f2a7224389)
(PR #17, Issue #16). Source tree: `ad6604601e052355510ac5140b69c10020c7b88c`.
The merged tree equals reviewed commit `e80989566137b0150e4ec61936901975612f506d`.

Wheel SHA-256: `ecee71d1176f8ca6d5992b1a6dd10fd34a71dab2cc0d8ef30301d13962cb265e`.
Built with `python -m build --wheel --no-isolation` from a clean archive of that tree.
No upstream source is patched or forked in AutoNavLog. Desktop dependencies remain
native; the public Pyodide prepared-data consumer needs NumPy and tzdata.

The historical `jma_msm_wind-0.2.1` wheel is unchanged and is no longer installed by
production, test images, or the Local asset build.
