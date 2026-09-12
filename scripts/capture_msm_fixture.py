"""Re-capture the fixed #125 MSM acceptance subset (requires netCDF4 and NumPy).

Maintenance-only script: never imported or invoked by the Local runtime/build/tests.
RISH keeps these forecast URLs for a limited time; committed fixtures are authoritative.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

out = Path(__file__).resolve().parents[1] / "tests/fixtures/msm"
out.mkdir(exist_ok=True)
manifest = {
    "description": (
        "Actual RISH JMA MSM forecast subsets; decoded using netCDF4 "
        "automatic scale/offset. No synthetic weather."
    ),
    "runs": [],
}
for hour in ("00", "03"):
    run_id = f"20260912{hour}0000"
    arrays = {}
    sources = []
    for kind in ("P", "S"):
        url = f"http://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/latest/20260912/MSM20260912{hour}{kind}.nc"
        print(url, flush=True)
        with Dataset(url + "#mode=bytes") as ds:
            lat = np.asarray(ds["lat"][:], dtype="float64")
            lon = np.asarray(ds["lon"][:], dtype="float64")
            yi = np.flatnonzero((lat >= 31.5) & (lat <= 34))
            xi = np.flatnonzero((lon >= 130) & (lon <= 132))
            ys = slice(int(yi[0]), int(yi[-1]) + 1)
            xs = slice(int(xi[0]), int(xi[-1]) + 1)
            times = num2date(ds["time"][:], ds["time"].units, only_use_cftime_datetimes=False)
            indices = [i for i, t in enumerate(times) if 3 <= t.hour <= 6 and t.day == 12]
            arrays[f"{kind}_lat"] = lat[ys]
            arrays[f"{kind}_lon"] = lon[xs]
            arrays[f"{kind}_times"] = np.array([times[i].isoformat() + "+00:00" for i in indices])
            variables = ("z", "u", "v", "temp") if kind == "P" else ("temp",)
            info = {"url": url, "time_units": ds["time"].units, "variables": {}}
            if kind == "P":
                arrays["levels"] = np.asarray(ds["p"][:10], dtype="int64")
            for name in variables:
                v = ds[name]
                values = v[indices, :10, ys, xs] if kind == "P" else v[indices, ys, xs]
                assert not np.ma.getmaskarray(values).any()
                arrays[f"{kind}_{name}"] = np.asarray(values, dtype="float64")
                info["variables"][name] = {
                    a: v.getncattr(a).item()
                    if isinstance(v.getncattr(a), np.generic)
                    else v.getncattr(a)
                    for a in v.ncattrs()
                }
                print(kind, name, values.shape, flush=True)
            sources.append(info)
    path = out / f"{run_id}.npz"
    np.savez_compressed(path, **arrays)
    manifest["runs"].append(
        {
            "id": run_id,
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sources": sources,
        }
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
