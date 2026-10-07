# Weather dependency

Production uses `jma_gpv_weather-0.6.0-py3-none-any.whl`, built from
[Yuto-24/jma-gpv-weather main](https://github.com/Yuto-24/jma-gpv-weather/commit/208f288fc95bcd1d1e951e7b62046b490d1b7958)
(PR #19, Issue #18). Source tree: `f03655972cba011669e000c8130740b587697d0a`.
The merged tree equals reviewed commit `82b778ccde92f7ecdbaa3855a333fecb91e9852e`.

Wheel SHA-256: `d008bb9a7125f6b69012562efffd1edd5d517345b2672359d3b55799fcc63787`.
Built with `python -m build --wheel --no-isolation` from a clean archive of that tree.
No upstream source is patched or forked in AutoNavLog. Desktop dependencies remain
native; the public MSM/GSM Pyodide prepared-data consumers need NumPy and tzdata.

The historical `jma_msm_wind-0.2.1` wheel is unchanged and is no longer installed by
production, test images, or the Local asset build.
